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
from moray_scribe.describer.inferencer import (
    InferenceReport,
    apply_inferred_edges_to_draft,
    merge_inferred_edges,
    run_inference_sync,
)
from moray_scribe.describer.sequencer import SequenceOutcome, describe_sequences
from moray_scribe.exceptions import DescriberError, ScribeError
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
    prune,
    trace_call_chains,
)
from moray_scribe.renderer import (
    diagram_to_mermaid,
    draft_to_markdown,
    sequence_flow_to_mermaid,
    validate_diagram,
)
from moray_scribe.scanner import extract_file, iter_python_files
from moray_scribe.schema import ArchitectureDraft, RawSymbolBundle, SymbolGraph

Mode = Literal["scan", "graph", "diagram", "doc"]

# Cap on gap notes carried into the rendered document (keeps ARCHITECTURE.md
# readable on 30k+ symbol repositories).
_KNOWN_GAP_LIMIT = 200


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

    The move-up is refused when the parent is a filesystem root (``/`` or a
    drive letter): scanning would then walk the whole volume — observed as a
    mysterious hang when a package-style project is mounted directly at
    ``/work`` in a container. The caller gets an explicit error instead.
    """
    if (path / "__init__.py").is_file():
        parent = path.parent
        if parent == parent.parent:
            raise ScribeError(
                f"{path} is a package mounted at a filesystem root; scanning its "
                "parent would walk the entire volume. Mount or copy it one level "
                "deeper (e.g. /work/project)."
            )
        return parent
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
    file_count = len(bundles)
    symbol_count = sum(len(bundle.symbols) for bundle in bundles)
    module_count = len({bundle.module for bundle in bundles})
    # Hubs and dead-code candidates are FULL-GRAPH statistics: pruning drops
    # dead candidates by design, so counting them on the pruned view would
    # make the number a function of the budget (0 dead at small budgets)
    # instead of a property of the codebase.
    full_graph = build_symbol_graph(bundles)
    annotate_degrees(full_graph)
    compute_pagerank(full_graph)
    hubs_count = _count_hubs(full_graph)
    dead_code_count = len(detect_dead_code(full_graph))
    graph = prune(full_graph, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)
    # The scanner bundles are the single largest allocation; free them before
    # serializing so peak RSS is graph-only.
    del bundles
    # Stamp provenance so the web viewer can resolve source files and titles.
    graph.metadata.update({"project": root.name, "root": str(root.resolve())})
    target = out_dir / "pruned-graph.json"
    target.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    summary = {
        "files_scanned": file_count,
        "symbols_count": symbol_count,
        "modules_count": module_count,
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "hubs_count": hubs_count,
        "dead_code_count": dead_code_count,
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


def _maybe_infer(
    full_graph: SymbolGraph,
    root: Path,
    cfg: Settings,
    dynamic_pool: list[tuple[str, str, int]],
    project_name: str,
) -> InferenceReport | None:
    """Run LLM dynamic inference when enabled, merging high-confidence edges.

    Inference must never kill a doc run: transport failures and unexpected
    errors degrade to the static graph with a warning (self-healing §2).
    """
    if not cfg.inference_enabled or not dynamic_pool:
        return None
    try:
        report = run_inference_sync(
            full_graph,
            cfg,
            root_path=str(root),
            dynamic_pool=dynamic_pool,
            project_name=project_name,
        )
        merged = merge_inferred_edges(full_graph, report)
        logger.info("dynamic inference merged {} edge(s)", merged)
        return report
    except ScribeError as exc:
        logger.warning("dynamic inference skipped: {}", exc)
    except Exception as exc:
        logger.warning("dynamic inference failed unexpectedly: {!r}", exc)
    return None


def _sequences_stage(
    chains: list[Any],
    client: OllamaClient,
    cfg: Settings,
    graph: SymbolGraph,
    project_name: str,
) -> SequenceOutcome | None:
    """Narrate traced chains as sequence flows; never kill the doc run.

    ``describe_sequences`` already degrades to deterministic engine flows, so
    this wrapper only guards against the unexpected.
    """
    if not chains:
        return None
    try:
        return describe_sequences(chains, client, cfg, graph=graph, project_name=project_name)
    except ScribeError as exc:
        logger.warning("sequence stage skipped: {}", exc)
    except Exception as exc:
        logger.warning("sequence stage failed unexpectedly: {!r}", exc)
    return None


def _format_gap_lines(inference: InferenceReport) -> list[str]:
    """One readable line per medium/low-confidence inference answer."""
    lines: list[str] = []
    for edge in sorted(inference.gaps, key=lambda item: (item.src, item.dst)):
        callee = f" (call `{edge.callee}`)" if edge.callee else ""
        lines.append(
            f"`{edge.src}`{callee} -> `{edge.dst}` [{edge.confidence}]: {edge.reason}"
        )
    return lines


def _last_errors(exc: DescriberError) -> list[str]:
    """Extract the validation-error section from a chat_json failure message.

    The message embeds the model's full last output; only the ``errors: [...]``
    tail is useful as correction pressure for the next ladder rung.
    """
    text = str(exc)
    marker = "errors: ["
    index = text.find(marker)
    if index == -1:
        return [text[:300]]
    return [text[index + len(marker) :][:1200].rstrip("]")]


def _describe_with_fallback(
    pruned: SymbolGraph,
    full_graph: SymbolGraph,
    client: OllamaClient,
    cfg: Settings,
    root: Path,
    project_name: str,
) -> ArchitectureDraft:
    """Describe the graph, degrading to smaller views on repeated failure.

    Large prompts can exhaust a small model even with a corrected context
    window; when the describer cannot produce a valid draft for the full
    budget, retries on half-sized and then 60-node hub-centered views beat
    failing the whole run. Validation errors from failed rungs travel with
    the retry (``prior_errors``) so the model corrects instead of repeating.
    The final artifact's stats still come from ``full_graph``.
    """
    ladder = [
        cfg.max_nodes,
        max(10, cfg.max_nodes // 2),
        max(10, min(60, cfg.max_nodes)),
    ]
    budgets: list[int] = []
    for budget in ladder:
        if budget not in budgets and budget <= len(full_graph.nodes):
            budgets.append(budget)
    if not budgets:  # graph smaller than the smallest budget: single attempt
        budgets = [len(full_graph.nodes)]

    prior_errors: list[str] = []
    last_error: DescriberError | None = None
    for index, budget in enumerate(budgets):
        try:
            if index == 0:
                return generate_architecture(
                    pruned,
                    client,
                    cfg,
                    project_name=project_name,
                    root_path=str(root),
                    prior_errors=prior_errors or None,
                )
            logger.warning(
                "describer failed at {} nodes; retrying with {} nodes (self-heal degrade)",
                budgets[index - 1],
                budget,
            )
            smaller = prune(
                full_graph,
                max_nodes=budget,
                max_edges=max(100, cfg.max_edges * budget // max(1, cfg.max_nodes)),
            )
            return generate_architecture(
                smaller,
                client,
                cfg,
                project_name=project_name,
                root_path=str(root),
                prior_errors=prior_errors or None,
            )
        except DescriberError as exc:
            last_error = exc
            prior_errors.extend(_last_errors(exc))
    assert last_error is not None  # the loop only ends after a failure
    raise last_error


def _run_doc(
    root: Path,
    path: Path,
    cfg: Settings,
    out_dir: Path,
    project_name: str,
) -> ScribeResult:
    """Full doc pipeline: scan -> analyze -> describe -> render markdown + mmd files."""
    dynamic_pool: list[tuple[str, str, int]] = []
    bundles = _scan_bundles(root, cfg)
    full_graph = build_symbol_graph(bundles, dynamic_out=dynamic_pool)
    # Frees the largest allocation before the long LLM stages begin; nothing
    # downstream reads the bundles again.
    del bundles
    annotate_degrees(full_graph)
    compute_pagerank(full_graph)
    entry_points = detect_entry_points(full_graph)
    if len(full_graph.nodes) > 300:
        logger.warning(
            "Graph has {} nodes; consider raising MORAY_SCRIBE_MAX_OUTPUT_TOKENS above {}",
            len(full_graph.nodes),
            cfg.max_output_tokens,
        )

    inference = _maybe_infer(full_graph, root, cfg, dynamic_pool, project_name)
    # Dead code is computed AFTER inference: a merged edge can resurrect an
    # unreachable candidate, and the report must not contradict the graph.
    dead_code = detect_dead_code(full_graph)

    # Trace business call chains AFTER inference so merged edges participate.
    chains = trace_call_chains(full_graph)

    pruned = prune(full_graph, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)
    pruned.metadata.update({"project": project_name, "root": str(root.resolve())})
    client = OllamaClient(
        host=cfg.ollama_host,
        timeout_s=cfg.request_timeout_s,
    )
    draft = _describe_with_fallback(pruned, full_graph, client, cfg, root, project_name)
    if inference is not None:
        injected = apply_inferred_edges_to_draft(draft, full_graph)
        logger.info("drew {} inferred edge(s) into diagrams", injected)

    sequence_outcome = _sequences_stage(chains, client, cfg, full_graph, project_name)

    core_hubs = sorted(
        ((node_id, full_graph.nodes[node_id].pagerank) for node_id in full_graph.nodes),
        key=lambda item: (-item[1], item[0]),
    )[:20]

    known_gaps = detect_known_gaps(full_graph)
    if len(known_gaps) > _KNOWN_GAP_LIMIT:
        # Huge repositories produce thousands of "no recorded calls" notes;
        # rendering them all bloats the document and the prompt context.
        remaining = len(known_gaps) - _KNOWN_GAP_LIMIT
        known_gaps = [
            *known_gaps[:_KNOWN_GAP_LIMIT],
            f"… 另有 {remaining} 条同类型缺口（详见 --infer 推断结果与源码）",
        ]
    if inference is not None and inference.gaps:
        known_gaps = [
            *known_gaps,
            *(f"[AI推断·未合图] {gap_text}" for gap_text in _format_gap_lines(inference)),
        ]

    markdown = draft_to_markdown(
        draft,
        graph=pruned,
        full_graph=full_graph,
        known_gaps=known_gaps,
        dead_code=dead_code,
        entry_points=entry_points,
        core_hubs=core_hubs,
        project_name=project_name,
        inference=inference,
        sequence_flows=sequence_outcome.flows if sequence_outcome else None,
        sequence_degraded=sequence_outcome.degraded if sequence_outcome else False,
    )
    md_path = out_dir / "ARCHITECTURE.md"
    md_path.write_text(markdown, encoding="utf-8")

    # Export the enriched graph next to the document so the interactive viewer
    # (`python web_viewer/serve.py out/pruned-graph.json`) opens the same picture.
    graph_path = out_dir / "pruned-graph.json"
    graph_path.write_text(pruned.model_dump_json(indent=2), encoding="utf-8")

    mmd_paths: list[str] = []
    for index, diagram in enumerate(draft.diagrams):
        kind_slug = diagram.kind.value
        mmd_path = out_dir / f"diagram-{index:02d}-{kind_slug}.mmd"
        mmd_path.write_text(diagram_to_mermaid(diagram), encoding="utf-8")
        mmd_paths.append(str(mmd_path))
        for warning in validate_diagram(diagram):
            logger.warning("diagram-{}: {}", index, warning)

    sequence_flows = sequence_outcome.flows if sequence_outcome else []
    for index, flow in enumerate(sequence_flows):
        seq_path = out_dir / f"sequence-{index:02d}.mmd"
        seq_path.write_text(sequence_flow_to_mermaid(flow), encoding="utf-8")
        mmd_paths.append(str(seq_path))

    logger.info("Architecture doc written to {}", md_path)
    summary: dict[str, Any] = {
        "modules_count": len(draft.modules),
        "hubs_count": len(core_hubs),
        "dead_code_count": len(dead_code),
        "diagrams_count": len(draft.diagrams),
        "sequences_count": len(sequence_flows),
        "sequences_degraded": bool(sequence_outcome and sequence_outcome.degraded),
        "md_path": str(md_path),
        "mmd_paths": mmd_paths,
        "describer": {
            "requests": client.requests,
            "prompt_tokens": client.prompt_tokens,
            "completion_tokens": client.completion_tokens,
        },
    }
    if inference is not None:
        summary["inference"] = {
            "dangling_calls": inference.candidates_total,
            "sent": inference.selected,
            "requests": inference.requests,
            "tokens": inference.total_tokens,
            "merged": len(inference.merged),
            "gaps": len(inference.gaps),
            "rejected": inference.rejected,
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
