"""Profile the deterministic pipeline (no LLM): time and RSS per stage.

Usage::

    python benchmarks/pipeline_profile.py E:/moray-scribe/.stress/django --label django
    python benchmarks/pipeline_profile.py path/to/pkg --out .stress/results/django-static.json

Stages mirror the real `moray-scribe doc` prefix:

1. scan      — libcst extraction for every Python file
2. graph     — build, PageRank, entry/dead-code detection
3. pruned    — budget pruning for the describer (the memory hot spot this
               release fixes)
4. render    — deterministic sections (routes + data flow) and JSON export

Peak RSS is sampled at 20 Hz by a background thread; the per-stage figure is
the peak observed while that stage ran. The harness is read-only over the repo.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from moray_scribe.config import Settings
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
    prune,
)
from moray_scribe.pipeline import resolve_scan_root
from moray_scribe.renderer.markdown import _section_dataflow, _section_routes
from moray_scribe.scanner import extract_file, iter_python_files
from moray_scribe.schema import RawSymbolBundle


@dataclass
class Stage:
    name: str
    seconds: float = 0.0
    peak_mb: float = 0.0
    detail: dict[str, object] = field(default_factory=dict)


class RssSampler:
    """Background RSS sampler reporting peak MB over a named window."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._peak = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._process.memory_info().rss
            except psutil.Error:  # pragma: no cover - process died
                return
            self._peak = max(self._peak, rss)
            self._stop.wait(0.05)

    def __enter__(self) -> RssSampler:
        self._peak = self._process.memory_info().rss
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    @property
    def peak_mb(self) -> float:
        return self._peak / (1024 * 1024)


def _python_files(root: Path, cfg: Settings) -> list[Path]:
    return list(
        iter_python_files(
            root,
            include=cfg.include,
            exclude=cfg.exclude,
            max_size_kb=cfg.max_file_size_kb,
        )
    )


def _count_lines(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                total += sum(1 for _ in handle)
        except OSError:
            continue
    return total


def profile(
    path: Path,
    *,
    max_nodes: int,
    max_edges: int,
    exclude: list[str] | None = None,
) -> dict[str, object]:
    cfg = Settings(max_nodes=max_nodes, max_edges=max_edges)
    if exclude:
        cfg = cfg.model_copy(update={"exclude": [*cfg.exclude, *exclude]})
    root = resolve_scan_root(path)
    files = _python_files(root, cfg)
    total_lines = _count_lines(files)

    stages: list[Stage] = []
    baseline_mb = psutil.Process().memory_info().rss / (1024 * 1024)

    # -- scan ---------------------------------------------------------------
    t0 = time.perf_counter()
    with RssSampler() as sampler:
        bundles: list[RawSymbolBundle] = [extract_file(p, root=root) for p in files]
    scan_seconds = time.perf_counter() - t0
    scan_peak = sampler.peak_mb
    symbol_count = sum(len(b.symbols) for b in bundles)
    stages.append(
        Stage(
            "scan",
            scan_seconds,
            scan_peak,
            {"files": len(files), "lines": total_lines, "symbols": symbol_count},
        )
    )

    # -- graph --------------------------------------------------------------
    dynamic_pool: list[tuple[str, str, int]] = []
    t0 = time.perf_counter()
    with RssSampler() as sampler:
        graph = build_symbol_graph(bundles, dynamic_out=dynamic_pool)
        annotate_degrees(graph)
        compute_pagerank(graph)
        entry_points = detect_entry_points(graph)
        dead_code = detect_dead_code(graph)
    graph_seconds = time.perf_counter() - t0
    stages.append(
        Stage(
            "graph",
            graph_seconds,
            sampler.peak_mb,
            {
                "nodes": graph.node_count,
                "edges": graph.edge_count,
                "dangling_calls": len(dynamic_pool),
                "entry_points": len(entry_points),
                "dead_candidates": len(dead_code),
            },
        )
    )

    # -- free bundles like the real pipeline does ---------------------------
    del bundles
    gc.collect()

    # -- prune --------------------------------------------------------------
    t0 = time.perf_counter()
    with RssSampler() as sampler:
        pruned = prune(graph, max_nodes=max_nodes, max_edges=max_edges)
    prune_seconds = time.perf_counter() - t0
    stages.append(
        Stage(
            "prune",
            prune_seconds,
            sampler.peak_mb,
            {"nodes": pruned.node_count, "edges": pruned.edge_count},
        )
    )

    # -- render deterministic sections -------------------------------------
    t0 = time.perf_counter()
    with RssSampler() as sampler:
        route_lines = _section_routes(graph)
        flow_lines = _section_dataflow(graph)
        payload = pruned.model_dump_json(indent=2)
    render_seconds = time.perf_counter() - t0
    stages.append(
        Stage(
            "render",
            render_seconds,
            sampler.peak_mb,
            {
                "route_rows": max(0, len(route_lines) - 4),
                "dataflow_rows": max(0, len(flow_lines) - 4),
                "graph_json_mb": len(payload.encode("utf-8")) / (1024 * 1024),
            },
        )
    )

    peak_mb = max(stage.peak_mb for stage in stages)
    return {
        "path": str(path),
        "root": str(root),
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "exclude": list(exclude or []),
        "lines_scanned": total_lines,
        "baseline_rss_mb": round(baseline_mb, 1),
        "peak_rss_mb": round(peak_mb, 1),
        "total_seconds": round(sum(stage.seconds for stage in stages), 2),
        "stages": [
            {
                "stage": stage.name,
                "seconds": round(stage.seconds, 3),
                "peak_mb": round(stage.peak_mb, 1),
                **stage.detail,
            }
            for stage in stages
        ],
    }


def _print_report(result: dict[str, object]) -> None:
    print(f"\n== {result['path']} ==")
    print(f"lines: {result['lines_scanned']:,}   peak RSS: {result['peak_rss_mb']} MB   "
          f"total: {result['total_seconds']}s")
    print(f"{'stage':<10}{'seconds':>10}{'peak MB':>10}  detail")
    for stage in result["stages"]:  # type: ignore[assignment]
        detail = {
            key: value
            for key, value in stage.items()
            if key not in {"stage", "seconds", "peak_mb"}
        }
        print(f"{stage['stage']:<10}{stage['seconds']:>10.3f}{stage['peak_mb']:>10.1f}  {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile the deterministic pipeline.")
    parser.add_argument("path", type=Path, help="Project directory or package to profile.")
    parser.add_argument("--label", default=None, help="Result label (default: folder name).")
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON result here.")
    parser.add_argument("--max-nodes", type=int, default=200)
    parser.add_argument("--max-edges", type=int, default=500)
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="PATTERN",
        help="Extra exclude glob, repeatable (e.g. '**/tests/**').",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        parser.error(f"path not found: {args.path}")
    result = profile(
        args.path,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        exclude=args.exclude,
    )
    result["label"] = args.label or args.path.name
    _print_report(result)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
