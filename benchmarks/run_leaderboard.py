"""v0.5 benchmark harness: 10 top Python projects, graph + doc, one JSON line each.

Usage::

    .venv/Scripts/python.exe benchmarks/run_leaderboard.py [--graph-only] [--projects ...]

For every project in ``.stress10/`` this runs, sequentially and in one process
(so the timing/RSS numbers are comparable):

1. ``graph`` mode (deterministic static analysis — timing + topology metrics);
2. ``doc`` mode (LLM describer + sequence stage over the REAL local Ollama).

Results append to ``.bench-data/leaderboard.json`` — one JSON line per project,
the source of truth for docs/benchmark-leaderboard.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from moray_scribe.config import Settings  # noqa: E402
from moray_scribe.pipeline import run_with_result  # noqa: E402

CORPUS_DIR = REPO_ROOT / ".stress10"
RESULTS_DIR = REPO_ROOT / ".bench-data"
RESULTS_FILE = RESULTS_DIR / "leaderboard.json"

PROJECTS = [
    "tqdm", "flask", "requests", "pandas", "scrapy",
    "click", "rich", "pydantic", "fastapi", "aiohttp",
]

EXCLUDES = ["**/test_*.py", "**/tests/**", "**/__pycache__/**", "**/.venv/**"]


def _rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def run_graph_record(
    project: str,
    cfg: Settings,
    out_dir: Path,
) -> dict[str, object]:
    """Run graph mode for one project and return its metrics record."""
    t0 = time.perf_counter()
    graph_result = run_with_result("graph", CORPUS_DIR / project, cfg, out_dir=out_dir / project)
    graph_seconds = time.perf_counter() - t0
    summary = graph_result.summary
    return {
        "project": project,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "graph_seconds": round(graph_seconds, 2),
        "files_scanned": summary["files_scanned"],
        "symbols_count": summary["symbols_count"],
        "nodes": summary["nodes"],
        "edges": summary["edges"],
        "hubs_count": summary["hubs_count"],
        "dead_code_count": summary["dead_code_count"],
        "graph_out": str(graph_result.out_path),
    }


def run_doc_record(project: str, cfg: Settings, out_dir: Path) -> dict[str, object]:
    """Run doc mode (LLM describer + sequences) and return its metrics record."""
    t1 = time.perf_counter()
    doc_result = run_with_result("doc", CORPUS_DIR / project, cfg, out_dir=out_dir / project)
    doc_seconds = time.perf_counter() - t1
    doc_summary = doc_result.summary
    describer = doc_summary.get("describer", {})
    return {
        "project": project,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": cfg.model_describer,
        "doc_seconds": round(doc_seconds, 2),
        "doc_md": str(doc_result.out_path),
        "diagrams_count": doc_summary.get("diagrams_count"),
        "sequences_count": doc_summary.get("sequences_count"),
        "sequences_degraded": doc_summary.get("sequences_degraded"),
        "describer_requests": describer.get("requests"),
        "describer_tokens": (describer.get("prompt_tokens", 0) or 0)
        + (describer.get("completion_tokens", 0) or 0),
        "peak_rss_mb": round(_rss_mb(), 1),
    }


def _append(record: dict[str, object]) -> None:
    """Append one JSON line to the results file (flushed immediately)."""
    with RESULTS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the 10-project leaderboard corpus.")
    parser.add_argument("--graph-only", action="store_true", help="Skip the LLM doc stage.")
    parser.add_argument("--projects", nargs="*", default=None, help="Subset to run.")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=8192,
        help="num_predict per describer call (4096 truncates big drafts mid-JSON).",
    )
    args = parser.parse_args(argv)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = Settings(
        _env_file=None,
        model_describer=args.model,
        request_timeout_s=900,
        # Doc view of 60 nodes: the ladder's proven-stable size for 7B models
        # (bigger views cost 10-20 min/rung and degenerate anyway). The
        # leaderboard's headline metrics are the static ones; doc_seconds
        # record the fast, single-rung LLM stage under this fixed budget.
        max_nodes=60,
        # 4096 truncates the pretty-printed draft JSON mid-object for larger
        # repos (observed live: 'Expecting property name enclosed in double
        # quotes' at the metadata tail) — 8192 fits a 60-node draft.
        max_output_tokens=args.max_output_tokens,
        exclude=EXCLUDES,
    )
    logger.add(
        RESULTS_DIR / "harness.log",
        level="INFO",
        encoding="utf-8",
        enqueue=False,
    )
    out_dir = RESULTS_DIR / "out"
    selected = args.projects or PROJECTS
    failures = 0
    for project in selected:
        # Graph first and flushed immediately: static metrics survive even if
        # the slow LLM stage wedges or the run is interrupted.
        try:
            graph_record = run_graph_record(project, cfg, out_dir)
            _append(graph_record)
            logger.info(
                "leaderboard | {} | graph={:.1f}s | {} nodes | {} dead",
                project,
                graph_record.get("graph_seconds", 0) or 0,
                graph_record.get("nodes"),
                graph_record.get("dead_code_count"),
            )
        except Exception as exc:
            failures += 1
            _append({"project": project, "error": repr(exc)[:300]})
            continue
        if args.graph_only:
            continue
        try:
            doc_record = run_doc_record(project, cfg, out_dir)
            _append(doc_record)
            logger.info(
                "leaderboard | {} | doc={:.1f}s | sequences={} degraded={} | tokens={}",
                project,
                doc_record.get("doc_seconds", 0) or 0,
                doc_record.get("sequences_count"),
                doc_record.get("sequences_degraded"),
                doc_record.get("describer_tokens"),
            )
        except Exception as exc:
            failures += 1
            _append({"project": project, "error": repr(exc)[:300]})
    logger.info("done, failures={}", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
