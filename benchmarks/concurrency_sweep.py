"""Sweep LLM inference concurrency and report throughput / GPU memory.

Usage::

    python benchmarks/concurrency_sweep.py E:/moray-scribe/.stress/flask \
        --levels 1,2,4,8 --calls 60 --batch 8 --model qwen2.5:7b

For each concurrency level the harness rebuilds the same dangling-call batch
set, runs the real inference stage (no edge merging, so the graph stays
identical), and records wall time, requests, tokens and sampled GPU memory.
A warm-up request loads the model so the first level does not pay model-load
cost. Results go to ``.stress/results/concurrency-<label>.json``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from moray_scribe.config import Settings
from moray_scribe.describer.inferencer import run_inference_sync
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
)
from moray_scribe.pipeline import resolve_scan_root
from moray_scribe.scanner import extract_file, iter_python_files


class GpuSampler:
    """Poll nvidia-smi for used VRAM; silent when no GPU is present."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak_mib = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _read(self) -> int | None:
        try:
            output = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            return max(int(line.strip()) for line in output.stdout.splitlines() if line.strip())
        except ValueError:
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            value = self._read()
            if value is not None:
                self.peak_mib = max(self.peak_mib, value)
            self._stop.wait(0.2)

    def __enter__(self) -> GpuSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


def _prepare(path: Path, cfg: Settings) -> tuple[object, list[tuple[str, str, int]], Path]:
    root = resolve_scan_root(path)
    bundles = [
        extract_file(p, root=root)
        for p in iter_python_files(
            root, include=cfg.include, exclude=cfg.exclude, max_size_kb=cfg.max_file_size_kb
        )
    ]
    dynamic: list[tuple[str, str, int]] = []
    graph = build_symbol_graph(bundles, dynamic_out=dynamic)
    annotate_degrees(graph)
    compute_pagerank(graph)
    return graph, dynamic, root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep inference concurrency.")
    parser.add_argument("path", type=Path, help="Project directory to analyse.")
    parser.add_argument("--levels", default="1,2,4,8", help="Comma-separated concurrency levels.")
    parser.add_argument("--calls", type=int, default=60, help="Dangling calls per level.")
    parser.add_argument("--batch", type=int, default=8, help="Call sites per request.")
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama model for inference.")
    parser.add_argument("--timeout", type=int, default=900, help="Per-request timeout (s).")
    parser.add_argument("--out", type=Path, default=None, help="JSON result path.")
    args = parser.parse_args(argv)

    levels = [int(item) for item in args.levels.split(",") if item.strip()]
    cfg = Settings(
        inference_enabled=True,
        model_inferencer=args.model,
        inference_max_calls=args.calls,
        inference_batch_size=args.batch,
        inference_concurrency=max(levels),
        request_timeout_s=args.timeout,
    )
    print(f"preparing graph from {args.path} …")
    graph, dynamic, root = _prepare(args.path, cfg)
    print(f"nodes={graph.node_count} edges={graph.edge_count} dangling={len(dynamic)}")

    results: list[dict[str, object]] = []
    for level in levels:
        cfg_level = cfg.model_copy(update={"inference_concurrency": level})
        started = time.perf_counter()
        with GpuSampler() as gpu:
            try:
                report = run_inference_sync(
                    graph,
                    cfg_level,
                    root_path=str(root),
                    dynamic_pool=dynamic,
                    project_name=args.path.name,
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started
                results.append(
                    {
                        "concurrency": level,
                        "error": repr(exc),
                        "seconds": round(elapsed, 2),
                    }
                )
                print(f"concurrency={level}: FAILED after {elapsed:.1f}s: {exc!r}")
                continue
        elapsed = time.perf_counter() - started
        entry = {
            "concurrency": level,
            "seconds": round(elapsed, 2),
            "requests": report.requests,
            "selected_calls": report.selected,
            "merged": len(report.merged),
            "gaps": len(report.gaps),
            "rejected": report.rejected,
            "prompt_tokens": report.prompt_tokens,
            "completion_tokens": report.completion_tokens,
            "calls_per_second": round(report.selected / elapsed, 3) if elapsed else None,
            "tokens_per_second": round(report.total_tokens / elapsed, 2) if elapsed else None,
            "peak_gpu_mib": gpu.peak_mib or None,
        }
        results.append(entry)
        print(
            f"concurrency={level:<2} {elapsed:7.1f}s  merged={entry['merged']:<3}"
            f" rejected={entry['rejected']:<3} tokens={report.total_tokens:<6}"
            f" tok/s={entry['tokens_per_second']:<7} gpu={entry['peak_gpu_mib']} MiB"
        )

    payload = {
        "project": str(args.path),
        "model": args.model,
        "calls": args.calls,
        "batch": args.batch,
        "dangling_pool": len(dynamic),
        "results": results,
    }
    out = args.out or (
        Path(".stress/results") / f"concurrency-{args.path.name}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
