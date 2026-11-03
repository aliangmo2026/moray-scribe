"""Stress-test harness: run the full moray-scribe chain over large real repos.

Usage:
    python benchmarks/stress_test.py [--projects django,sqlalchemy,flask] [--skip-clone]

For each project it:
1. shallow-clones the repo into .stress/<name> (unless --skip-clone),
2. runs scan -> graph -> (LLM dynamic inference) -> describer -> render,
   timing every stage and sampling process RSS with psutil,
3. dumps per-project JSON results to .stress/results/<name>.json,
4. samples up to 20 merged inference edges for the accuracy audit
   (.stress/results/<name>-sample.json).

The harness never mutates moray-scribe state; it only reads the pipeline API.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from moray_scribe.config import Settings
from moray_scribe.describer import OllamaClient
from moray_scribe.describer.inferencer import (
    apply_inferred_edges_to_draft,
    merge_inferred_edges,
    run_inference_sync,
)
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
    prune,
)
from moray_scribe.pipeline import resolve_scan_root
from moray_scribe.renderer import diagram_to_mermaid, draft_to_markdown, validate_diagram
from moray_scribe.scanner import extract_file, iter_python_files
from moray_scribe.schema import RawSymbolBundle, SymbolGraph

REPO_ROOT = Path(__file__).resolve().parent.parent
STRESS_DIR = REPO_ROOT / ".stress"
RESULTS_DIR = STRESS_DIR / "results"

PROJECTS = {
    "django": {
        "url": "https://github.com/django/django.git",
        "scan_dir": "django",  # scan the package dir; resolver walks the repo root
        "exclude": [
            "**/test_*.py",
            "**/tests/**",
            "**/docs/**",
            "**/benchmarks/**",
            "**/scripts/**",
        ],
    },
    "sqlalchemy": {
        "url": "https://github.com/sqlalchemy/sqlalchemy.git",
        "scan_dir": "lib/sqlalchemy",
        "exclude": ["**/test_*.py", "**/tests/**", "**/examples/**", "**/docs/**"],
    },
    "flask": {
        "url": "https://github.com/pallets/flask.git",
        "scan_dir": "src/flask",
        "exclude": ["**/test_*.py", "**/tests/**", "**/docs/**", "**/examples/**"],
    },
}


@dataclass
class StageRecord:
    name: str
    seconds: float = 0.0
    peak_rss_mb: float = 0.0


@dataclass
class ProjectResult:
    name: str
    files: int = 0
    symbols: int = 0
    nodes: int = 0
    edges: int = 0
    pruned_nodes: int = 0
    pruned_edges: int = 0
    dangling_pool: int = 0
    stages: list[StageRecord] = field(default_factory=list)
    inference: dict[str, object] = field(default_factory=dict)
    describer: dict[str, object] = field(default_factory=dict)
    status: str = "ok"
    error: str = ""


class MemorySampler:
    """Background RSS sampler for the current process."""

    def __init__(self, interval: float = 0.2) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.peak_rss = psutil.Process().memory_info().rss
        self.baseline_rss = self.peak_rss

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        process = psutil.Process()
        while not self._stop.is_set():
            try:
                rss = process.memory_info().rss
                if rss > self.peak_rss:
                    self.peak_rss = rss
            except (psutil.Error, OSError):
                pass
            self._stop.wait(self._interval)


def clone(name: str, url: str) -> Path:
    target = STRESS_DIR / name
    if (target / ".git").is_dir():
        print(f"[clone] {name}: already present, skipping")
        return target
    print(f"[clone] {name} from {url} (shallow)")
    start = time.perf_counter()
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target)],
        check=True,
        capture_output=True,
    )
    print(f"[clone] {name}: done in {time.perf_counter() - start:.1f}s")
    return target


def scan_bundles(root: Path, cfg: Settings) -> list[RawSymbolBundle]:
    bundles: list[RawSymbolBundle] = []
    for path in iter_python_files(
        root, include=cfg.include, exclude=cfg.exclude, max_size_kb=cfg.max_file_size_kb
    ):
        bundles.append(extract_file(path, root=root))
    return bundles


def record(stages: list[StageRecord], name: str, started: float, sampler: MemorySampler) -> None:
    record_item = StageRecord(
        name=name,
        seconds=time.perf_counter() - started,
        peak_rss_mb=sampler.peak_rss / (1024 * 1024),
    )
    stages.append(record_item)
    print(
        f"  [{name}] {record_item.seconds:.1f}s, "
        f"process peak {record_item.peak_rss_mb:.0f} MB"
    )


def sample_accuracy(graph: SymbolGraph, root: Path, k: int = 20) -> list[dict[str, object]]:
    """Deterministically sample merged edges and attach cheap evidence checks.

    The verdict itself is filled in by a human reviewing the JSON; the harness
    only gathers the material: the call site snippet and whether the target
    name appears in the caller's file.
    """
    inferred = sorted(
        (edge for edge in graph.edges if edge.inferred),
        key=lambda edge: (edge.source, edge.target),
    )
    if not inferred:
        return []
    step = max(1, len(inferred) // k)
    sample = inferred[::step][:k]
    out: list[dict[str, object]] = []
    for edge in sample:
        src_node = graph.nodes.get(edge.source)
        dst_node = graph.nodes.get(edge.target)
        entry: dict[str, object] = {
            "src": edge.source,
            "dst": edge.target,
            "callee": edge.reason,
            "confidence": edge.confidence,
            "reason": edge.reason,
        }
        if src_node is not None:
            entry["src_file"] = str(root / src_node.file)
            entry["src_line"] = src_node.lineno
        if dst_node is not None:
            entry["dst_file"] = str(root / dst_node.file)
            entry["dst_line"] = dst_node.lineno
        out.append(entry)
    return out


def run_project(name: str, meta: dict[str, str], cfg: Settings) -> ProjectResult:
    repo = clone(name, meta["url"])
    root = resolve_scan_root(repo / meta["scan_dir"])
    print(f"[{name}] scan root: {root}")
    if meta.get("exclude"):
        cfg = cfg.model_copy(update={"exclude": list(meta["exclude"])})
    result = ProjectResult(name=name)
    stages = result.stages
    sampler = MemorySampler()
    sampler.start()
    try:
        t0 = time.perf_counter()
        bundles = scan_bundles(root, cfg)
        result.files = len(bundles)
        result.symbols = sum(len(b.symbols) for b in bundles)
        record(stages, "scan", t0, sampler)

        t0 = time.perf_counter()
        pool: list[tuple[str, str, int]] = []
        graph = build_symbol_graph(bundles, dynamic_out=pool)
        annotate_degrees(graph)
        compute_pagerank(graph)
        result.dangling_pool = len(pool)
        result.nodes = graph.node_count
        result.edges = graph.edge_count
        record(stages, "graph", t0, sampler)

        t0 = time.perf_counter()
        report = run_inference_sync(
            graph, cfg, root_path=str(root), dynamic_pool=pool, project_name=name
        )
        merged = merge_inferred_edges(graph, report)
        result.inference = {
            "dangling_calls": report.candidates_total,
            "sent": report.selected,
            "requests": report.requests,
            "prompt_tokens": report.prompt_tokens,
            "completion_tokens": report.completion_tokens,
            "merged": len(report.merged),
            "merged_into_graph": merged,
            "gaps": len(report.gaps),
            "rejected": report.rejected,
        }
        record(stages, "infer", t0, sampler)

        t0 = time.perf_counter()
        pruned = prune(graph, max_nodes=cfg.max_nodes, max_edges=cfg.max_edges)
        result.pruned_nodes = pruned.node_count
        result.pruned_edges = pruned.edge_count
        record(stages, "prune", t0, sampler)

        t0 = time.perf_counter()
        client = OllamaClient(host=cfg.ollama_host, timeout_s=cfg.request_timeout_s)
        # Same self-heal ladder as the product pipeline (_run_doc): degrade the
        # view size when the describer cannot produce a valid draft.
        from moray_scribe.pipeline import _describe_with_fallback

        draft = _describe_with_fallback(pruned, graph, client, cfg, root, name)
        result.describer = {
            "requests": client.requests,
            "prompt_tokens": client.prompt_tokens,
            "completion_tokens": client.completion_tokens,
            "diagrams": len(draft.diagrams),
            "modules": len(draft.modules),
        }
        record(stages, "describe", t0, sampler)

        t0 = time.perf_counter()
        injected = apply_inferred_edges_to_draft(draft, graph)
        out_dir = RESULTS_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        dead_code = detect_dead_code(graph)
        entry_points = detect_entry_points(graph)
        core_hubs = sorted(
            ((node_id, graph.nodes[node_id].pagerank) for node_id in graph.nodes),
            key=lambda item: (-item[1], item[0]),
        )[:20]
        markdown = draft_to_markdown(
            draft,
            graph=pruned,
            full_graph=graph,
            known_gaps=[],
            dead_code=dead_code,
            entry_points=entry_points,
            core_hubs=core_hubs,
            project_name=name,
            inference=report,
        )
        (out_dir / "ARCHITECTURE.md").write_text(markdown, encoding="utf-8")
        for index, diagram in enumerate(draft.diagrams):
            slug = diagram.kind.value
            (out_dir / f"diagram-{index:02d}-{slug}.mmd").write_text(
                diagram_to_mermaid(diagram), encoding="utf-8"
            )
        warnings = [w for d in draft.diagrams for w in validate_diagram(d)]
        result.inference["diagram_edges_injected"] = injected
        result.inference["diagram_warnings"] = len(warnings)
        record(stages, "render", t0, sampler)

        t0 = time.perf_counter()
        sample = sample_accuracy(graph, root)
        (RESULTS_DIR / f"{name}-sample.json").write_text(
            json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        record(stages, "sample", t0, sampler)
    except Exception as exc:
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        print(f"[{name}] FAILED: {result.error}")
        import traceback

        traceback.print_exc()
    finally:
        sampler.stop()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", default="django,sqlalchemy,flask")
    parser.add_argument("--skip-clone", action="store_true")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Settings(_env_file=None)
    print(
        f"settings: describer={cfg.model_describer} inferencer={cfg.model_inferencer} "
        f"max_nodes={cfg.max_nodes} max_calls={cfg.inference_max_calls} "
        f"batch={cfg.inference_batch_size} concurrency={cfg.inference_concurrency} "
        f"timeout={cfg.request_timeout_s}s"
    )

    all_results: dict[str, ProjectResult] = {}
    for name in args.projects.split(","):
        name = name.strip()
        if not name:
            continue
        meta = PROJECTS[name]
        if args.skip_clone:
            meta = dict(meta)
            meta["url"] = ""  # clone() short-circuits when the dir exists
        print(f"=== project: {name} ===")
        result = run_project(name, meta, cfg)
        all_results[name] = result
        (RESULTS_DIR / f"{name}.json").write_text(
            json.dumps(
                {
                    **{k: v for k, v in result.__dict__.items() if k != "stages"},
                    "stages": [s.__dict__ for s in result.stages],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    print("\n=== summary ===")
    for name, result in all_results.items():
        total = sum(s.seconds for s in result.stages)
        print(
            f"{name}: {result.status} files={result.files} nodes={result.nodes} "
            f"edges={result.edges} merged={result.inference.get('merged', 0)} "
            f"total={total:.0f}s"
        )
    return 0 if all(r.status == "ok" for r in all_results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
