"""Stage orchestration for scan, graph, diagram and doc modes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from moray_scribe.config import Settings
from moray_scribe.describer import (
    OllamaClient,
    detect_known_gaps,
    generate_architecture,
)
from moray_scribe.exceptions import ScribeError
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
    prune,
)
from moray_scribe.renderer import (
    diagram_to_mermaid,
    draft_to_markdown,
    validate_diagram,
)
from moray_scribe.scanner import extract_file, iter_python_files
from moray_scribe.schema import RawSymbolBundle, SymbolGraph

Mode = Literal["scan", "graph", "diagram", "doc"]


@dataclass
class ScribeResult:
    """Structured outcome of one pipeline run: artifact path plus a summary.

    The summary powers MCP tool responses (which must stay small) without
    re-reading artifacts.
    """

    mode: Mode
    out_path: Path | None
    summary: dict[str, Any] = field(default_factory=dict)


def resolve_scan_root(path: Path) -> Path:
    """Return the directory to scan for ``path``.

    When ``path`` is itself a package (contains ``__init__.py``), the scanner
    cannot derive a module name for the package root, so its parent is used
    instead. Any other path is returned unchanged.
    """
    if (path / "__init__.py").is_file():
        return path.parent
    return path


def _scan_bundles(root: Path, cfg: Settings) -> list[RawSymbolBundle]:
    """Walk ``root`` and extract one bundle per Python file."""
    bundles: list[RawSymbolBundle] = []
    for path in iter_python_files(
        root,
        include=cfg.include,
        exclude=cfg.exclude,
        max_size_kb=cfg.max_file_size_kb,
    ):
        bundles.append(extract_file(path, root=root))
    logger.info("scanned {} file(s) under {}", len(bundles), root)
    return bundles


def _analyzed_graph(bundles: list[RawSymbolBundle], cfg: Settings) -> SymbolGraph:
    """Build, rank and prune a symbol graph for the given budget."""
    graph = build_symbol_graph(bundles)
    compute_pagerank(graph)
    return prune(graph, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)


def _count_hubs(graph: SymbolGraph) -> int:
    """Count core-hub nodes (top-20% PageRank above a 0.001 floor)."""
    scores = sorted((node.pagerank for node in graph.nodes.values()), reverse=True)
    if not scores:
        return 0
    cutoff = min(int(len(scores) * 0.2), len(scores) - 1)
    threshold = scores[cutoff]
    return sum(
        1
        for node in graph.nodes.values()
        if node.pagerank >= threshold and node.pagerank > 0.001
    )


def _resolve_out_dir(path: Path, cfg: Settings, out_dir: Path | None) -> Path:
    if out_dir is not None:
        return out_dir
    base = cfg.out_dir
    return base if base.is_absolute() else path.resolve().parent / base


def _run_scan(root: Path, cfg: Settings, out_dir: Path) -> ScribeResult:
    bundles = _scan_bundles(root, cfg)
    target = out_dir / "symbol-graph.json"
    payload = [bundle.model_dump(mode="json") for bundle in bundles]
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "files_scanned": len(bundles),
        "symbols_count": sum(len(bundle.symbols) for bundle in bundles),
        "modules_count": len({bundle.module for bundle in bundles}),
        "out_path": str(target),
    }
    return ScribeResult(mode="scan", out_path=target, summary=summary)


def _run_graph(root: Path, cfg: Settings, out_dir: Path) -> ScribeResult:
    bundles = _scan_bundles(root, cfg)
    graph = _analyzed_graph(bundles, cfg)
    target = out_dir / "pruned-graph.json"
    target.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    summary = {
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "hubs_count": _count_hubs(graph),
        "dead_code_count": sum(1 for node in graph.nodes.values() if node.is_dead_candidate),
        "out_path": str(target),
    }
    return ScribeResult(mode="graph", out_path=target, summary=summary)


def _run_diagram(
    root: Path,
    path: Path,
    cfg: Settings,
    out_dir: Path,
    project_name: str,
) -> ScribeResult:
    bundles = _scan_bundles(root, cfg)
    graph = _analyzed_graph(bundles, cfg)
    client = OllamaClient(host=cfg.ollama_host, timeout_s=cfg.request_timeout_s)
    draft = generate_architecture(
        graph, client, cfg, project_name=project_name, root_path=str(root)
    )
    target = out_dir / "architecture-draft.json"
    target.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
    summary = {
        "diagrams_count": len(draft.diagrams),
        "first_kind": draft.diagrams[0].kind.value if draft.diagrams else None,
        "nodes_in_first": len(draft.diagrams[0].nodes) if draft.diagrams else 0,
        "out_path": str(target),
    }
    return ScribeResult(mode="diagram", out_path=target, summary=summary)


def _run_doc(
    root: Path,
    path: Path,
    cfg: Settings,
    out_dir: Path,
    project_name: str,
) -> ScribeResult:
    """Full doc pipeline: scan -> analyze -> describe -> render markdown + mmd files."""
    bundles = _scan_bundles(root, cfg)
    full_graph = build_symbol_graph(bundles)
    annotate_degrees(full_graph)
    compute_pagerank(full_graph)
    dead_code = detect_dead_code(full_graph)
    entry_points = detect_entry_points(full_graph)
    if len(full_graph.nodes) > 300:
        logger.warning(
            "Graph has {} nodes; consider raising MORAY_SCRIBE_MAX_OUTPUT_TOKENS above {}",
            len(full_graph.nodes),
            cfg.max_output_tokens,
        )

    pruned = prune(full_graph, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)
    client = OllamaClient(
        host=cfg.ollama_host,
        timeout_s=cfg.request_timeout_s,
    )
    draft = generate_architecture(
        pruned, client, cfg, project_name=project_name, root_path=str(root)
    )

    core_hubs = sorted(
        ((node_id, full_graph.nodes[node_id].pagerank) for node_id in full_graph.nodes),
        key=lambda item: (-item[1], item[0]),
    )[:20]

    markdown = draft_to_markdown(
        draft,
        graph=pruned,
        full_graph=full_graph,
        known_gaps=detect_known_gaps(full_graph),
        dead_code=dead_code,
        entry_points=entry_points,
        core_hubs=core_hubs,
        project_name=project_name,
    )
    md_path = out_dir / "ARCHITECTURE.md"
    md_path.write_text(markdown, encoding="utf-8")

    mmd_paths: list[str] = []
    for index, diagram in enumerate(draft.diagrams):
        kind_slug = diagram.kind.value
        mmd_path = out_dir / f"diagram-{index:02d}-{kind_slug}.mmd"
        mmd_path.write_text(diagram_to_mermaid(diagram), encoding="utf-8")
        mmd_paths.append(str(mmd_path))
        for warning in validate_diagram(diagram):
            logger.warning("diagram-{}: {}", index, warning)

    logger.info("Architecture doc written to {}", md_path)
    summary = {
        "modules_count": len(draft.modules),
        "hubs_count": len(core_hubs),
        "dead_code_count": len(dead_code),
        "diagrams_count": len(draft.diagrams),
        "md_path": str(md_path),
        "mmd_paths": mmd_paths,
    }
    return ScribeResult(mode="doc", out_path=md_path, summary=summary)


def run_with_result(
    mode: Mode,
    path: Path,
    cfg: Settings,
    *,
    out_dir: Path | None = None,
    project_name: str | None = None,
) -> ScribeResult:
    """Execute one pipeline mode and return the artifact path plus a summary.

    ``run()`` is a thin wrapper over this; MCP tools use the structured summary
    directly instead of re-reading artifacts.
    """
    root = resolve_scan_root(path)
    target_dir = _resolve_out_dir(path, cfg, out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = project_name or path.name

    try:
        if mode == "scan":
            return _run_scan(root, cfg, target_dir)
        if mode == "graph":
            return _run_graph(root, cfg, target_dir)
        if mode == "diagram":
            return _run_diagram(root, path, cfg, target_dir, name)
        if mode == "doc":
            return _run_doc(root, path, cfg, target_dir, name)
        raise ValueError(f"unknown mode: {mode}")
    except ScribeError:
        raise
    except OSError as exc:
        raise ScribeError(f"[{mode}] {exc}") from exc


def run(
    mode: Mode,
    path: Path,
    cfg: Settings,
    *,
    out_dir: Path | None = None,
    project_name: str | None = None,
) -> Path:
    """Execute one pipeline mode and return the primary artifact path."""
    result = run_with_result(mode, path, cfg, out_dir=out_dir, project_name=project_name)
    if result.out_path is None:  # pragma: no cover - all modes set out_path
        raise ScribeError(f"[{mode}] produced no artifact")
    return result.out_path
