"""Work Buddy serverless entry point for moray-scribe static analysis.

The cloud sandbox has no local filesystem access and no Ollama, so this module
exposes only the pure static-analysis path (scan + graph). Callers pass Python
source as an in-memory ``files`` mapping; the result is a JSON-serializable dict.
"""

from __future__ import annotations

import json
from typing import Any

from moray_scribe.grapher.analyzer import (
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
)
from moray_scribe.grapher.builder import build_symbol_graph
from moray_scribe.scanner.extractor import extract_source

# Hard cap so a huge repository cannot blow up the response payload.
MAX_HUBS = 50


def _derive_module(rel_path: str) -> str | None:
    """Mirror ``scanner.extractor._derive_module`` for a flat relative path.

    Returns ``None`` when no module name can be derived: a non-``.py`` key, or a
    top-level ``__init__.py`` which has no package context in this protocol
    (the real walker raises ``ScanError`` for the same situation).
    """
    posix = rel_path.replace("\\", "/")
    parts = [part for part in posix.split("/") if part not in ("", ".")]
    if not parts:
        return None
    stem = parts[-1]
    if not stem.endswith(".py"):
        return None
    parts[-1] = stem[: -len(".py")]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """云端调用入口。

    ``event['files'] = {"pkg/a.py": "def foo(): pass", ...}`` — keys are
    scan-root-relative posix paths, values are the full file contents as
    strings. Only the static analysis (scan + graph) pipeline runs here; the
    Ollama-backed describer/renderer are not part of this package.
    """
    if isinstance(event, str):
        try:
            event = json.loads(event)
        except json.JSONDecodeError:
            return {"error": "event must be a JSON object with a 'files' dictionary."}
    if not isinstance(event, dict):
        return {"error": "event must be a dictionary with a 'files' dictionary."}

    files = event.get("files", {})
    if not isinstance(files, dict) or not files:
        return {
            "error": "No files provided. Please provide a dictionary of file paths to contents."
        }

    try:
        top_hubs = int(event.get("top_hubs", 10))
    except (TypeError, ValueError):
        top_hubs = 10
    top_hubs = max(1, min(top_hubs, MAX_HUBS))

    bundles = []
    skipped: list[dict[str, str]] = []
    for path, content in files.items():
        if not isinstance(path, str):
            skipped.append({"path": repr(path), "error": "path is not a string"})
            continue
        module_name = _derive_module(path)
        if module_name is None:
            skipped.append(
                {
                    "path": path,
                    "error": "cannot derive a module name (non-.py file or top-level __init__.py)",
                }
            )
            continue
        if not isinstance(content, str):
            skipped.append({"path": path, "error": "content is not a string"})
            continue
        try:
            bundle = extract_source(content, module=module_name, file=path.replace("\\", "/"))
        except Exception as exc:  # parse errors are per-file, never fatal
            skipped.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        bundles.append(bundle)

    if not bundles:
        return {
            "status": "error",
            "error": "No Python files could be parsed.",
            "skipped": skipped,
        }

    graph = build_symbol_graph(bundles)
    compute_pagerank(graph)

    dead_code = detect_dead_code(graph)
    entry_points = detect_entry_points(graph)

    hubs = sorted(
        ((node_id, node.pagerank or 0.0) for node_id, node in graph.nodes.items()),
        key=lambda item: item[1],
        reverse=True,
    )[:top_hubs]

    return {
        "status": "success",
        "summary": {
            "total_files_parsed": len(bundles),
            "total_files_skipped": len(skipped),
            "total_symbols": len(graph.nodes),
            "total_edges": len(graph.edges),
            "core_hubs": [{"id": node_id, "score": round(score, 6)} for node_id, score in hubs],
            "dead_code_candidates": dead_code,
            "entry_points": entry_points,
        },
        "skipped": skipped,
    }
