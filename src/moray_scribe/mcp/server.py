"""MCP stdio server exposing moray-scribe as six tools for agent hosts.

Design rules (see the project spec):
- Context economy: executing tools write artifacts and return small summaries;
  readers slice/paginate so no response exceeds ~8KB.
- Tool descriptions are prompts: every description states what the tool does,
  when to use it, inputs and outputs.
- Failures carry an actionable ``hint:`` so the calling model knows the next step.

Only filesystem paths are accepted as inputs — no tool fetches a client-supplied
URL, so there is no SSRF surface. The Ollama host comes from operator config
(``MORAY_SCRIBE_OLLAMA_HOST``, default ``http://localhost:11434``) and talks to
the local model by design; it is validated to be http/https but is intentionally
allowed to be loopback because a local model is the product's whole premise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import ollama
from pydantic import ValidationError

from moray_scribe import __version__
from moray_scribe.config import Settings, load_settings
from moray_scribe.exceptions import DescriberError, ScribeError
from moray_scribe.pipeline import run_with_result
from moray_scribe.schema import SymbolGraph

# The SDK renamed FastMCP -> MCPServer in 2.x; "mcp>=1.0" allows both, so import
# whichever name exists while keeping one `app = FastMCP(...)` below.
try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined]
except ModuleNotFoundError as _exc:
    if _exc.name != "mcp.server.fastmcp":
        raise
    from mcp.server.mcpserver import MCPServer as FastMCP

_MAX_READ_BYTES = 8000
_MAX_SYMBOL_LIMIT = 100
app = FastMCP("moray-scribe")


class ToolHintError(Exception):
    """Tool failure whose message always ends with an actionable ``hint:`` line."""

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(f"{message}\nhint: {hint}")


def _check_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.exists():
        raise ToolHintError(
            f"path does not exist: {path_str}",
            "verify the path or list the workspace root first",
        )
    return path


def _http_scheme(host: str) -> bool:
    return host.startswith("http://") or host.startswith("https://")


def _ollama_status(host: str, model: str, timeout_s: int) -> tuple[bool, bool]:
    """Return (reachable, model_available) for the configured Ollama host."""
    if not _http_scheme(host):
        return False, False
    try:
        response = ollama.Client(host=host, timeout=timeout_s).list()
    except Exception:  # any failure means "not reachable"
        return False, False
    names = [str(getattr(entry, "model", "")) for entry in response.models]
    base = model.split(":", 1)[0]
    available = any(name == model or name.startswith(f"{base}:") for name in names)
    return True, available


def _out_writable(out_dir: Path) -> bool:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".scribe_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _wrap_pipeline_error(exc: Exception, cfg: Settings, mode: str) -> ToolHintError:
    if isinstance(exc, DescriberError):
        return ToolHintError(
            str(exc),
            f"ask the user to start Ollama and pull model {cfg.model_describer}",
        )
    return ToolHintError(
        str(exc),
        f"verify the {mode} target is a Python project and re-run scribe_scan first",
    )


@app.tool(
    description=(
        "Check whether moray-scribe is ready: Ollama reachable, configured model "
        "available, and the output directory writable. Returns a JSON object with "
        "ollama_reachable, model_available, out_writable (plus host/model details). "
        "USE THIS FIRST when scribe_doc failed — it isolates environment issues "
        "(Ollama down, model missing, unwritable output) from product issues. "
        "Never throws; reports status fields instead."
    )
)
def scribe_health() -> dict[str, Any]:
    """Report Ollama/model/output readiness as a structured status object."""
    cfg = load_settings()
    reachable, available = _ollama_status(
        cfg.ollama_host, cfg.model_describer, cfg.request_timeout_s
    )
    out_base = cfg.out_dir
    resolved_out = out_base if out_base.is_absolute() else Path.cwd() / out_base
    return {
        "ollama_reachable": reachable,
        "model_available": available,
        "out_writable": _out_writable(resolved_out),
        "ollama_host": cfg.ollama_host,
        "model": cfg.model_describer,
        "out_dir": str(resolved_out),
        "version": __version__,
    }


@app.tool(
    description=(
        "Scan a Python codebase with libcst and extract its static symbol inventory "
        "(classes, functions, methods, imports, call sites) to out/symbol-graph.json. "
        "Returns a summary: files_scanned, symbols_count, modules_count, out_path. "
        "USE THIS FIRST before scribe_graph or scribe_doc — it is the fastest way to "
        "know if the project is scannable (syntax errors surface here). Does NOT call "
        "any LLM. Safe to run on projects of any size."
    )
)
def scribe_scan(
    path: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    """Scan ``path`` (root dir or single .py) and return a symbol-count summary."""
    cfg = load_settings()
    if include is not None:
        cfg = cfg.model_copy(update={"include": include})
    if exclude is not None:
        cfg = cfg.model_copy(update={"exclude": exclude})
    target = _check_path(path)
    try:
        result = run_with_result("scan", target, cfg)
    except ScribeError as exc:
        raise _wrap_pipeline_error(exc, cfg, "scan") from exc
    return result.summary


@app.tool(
    description=(
        "Build a SymbolGraph from a scan, run PageRank to find core hubs, detect "
        "dead-code candidates and entry points, then prune to a token budget. Writes "
        "out/pruned-graph.json. Returns nodes, edges, hubs_count, dead_code_count, "
        "out_path. USE THIS when the user asks 'what is the core of this codebase' or "
        "before generating an architecture diagram. Does NOT call any LLM."
    )
)
def scribe_graph(path: str, max_nodes: int = 200) -> dict[str, Any]:
    """Build/analyze/prune the symbol graph for ``path`` and return a summary."""
    cfg = load_settings().model_copy(update={"max_nodes": max_nodes})
    target = _check_path(path)
    try:
        result = run_with_result("graph", target, cfg)
    except ScribeError as exc:
        raise _wrap_pipeline_error(exc, cfg, "graph") from exc
    return result.summary


@app.tool(
    description=(
        "THE main entry point. Generates ARCHITECTURE.md (layered doc with Mermaid "
        "diagrams, module business notes, and engine-verified stats: core hubs, dead "
        "code, known gaps) for a legacy Python project. Output separates AI-inferred "
        "content from deterministic analysis so the reader knows what to trust. "
        "REQUIRES local Ollama with the configured model (default qwen2.5-coder:14b); "
        "if Ollama is unavailable it fails with an actionable hint. Returns md_path, "
        "mmd_paths and a summary — NOT the full doc text (use scribe_read_artifact "
        "to read it in chunks)."
    )
)
def scribe_doc(
    path: str,
    out_dir: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Generate ARCHITECTURE.md + per-diagram .mmd files; return paths + summary."""
    cfg = load_settings()
    target = _check_path(path)
    try:
        result = run_with_result(
            "doc",
            target,
            cfg,
            out_dir=Path(out_dir) if out_dir else None,
            project_name=project_name,
        )
    except ScribeError as exc:
        raise _wrap_pipeline_error(exc, cfg, "doc") from exc
    return result.summary


def _slice_section(text: str, section: str, max_bytes: int) -> str:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("#") and section in line:
            start = index
            break
    if start is None:
        raise ToolHintError(
            f"section not found: {section}",
            "pass the exact heading text, or call without section to read from the top",
        )
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("#"):
            heading_level = len(line) - len(line.lstrip("#"))
            if heading_level <= level:
                end = index
                break
    return "\n".join(lines[start:end])


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{kept}\n\n... [truncated at {max_bytes} bytes]"


@app.tool(
    description=(
        "Read a generated artifact (ARCHITECTURE.md or a .mmd file) from a previous "
        "scribe_doc / scribe_diagram run, in chunks to protect your context window. "
        "Pass 'section' with the exact heading text (e.g. '核心枢纽', '死代码候选') to "
        "extract just that section; omit it to read from the top. Returns at most "
        "max_bytes (default 8000) per call and notes truncation. Prefer section-sliced "
        "reads over full reads."
    )
)
def scribe_read_artifact(
    path: str,
    section: str | None = None,
    max_bytes: int = _MAX_READ_BYTES,
) -> str:
    """Return a (possibly section-sliced, size-capped) chunk of an artifact."""
    target = _check_path(path)
    if max_bytes < 1:
        raise ToolHintError(
            f"max_bytes must be >= 1, got {max_bytes}",
            "use a positive max_bytes, e.g. 8000",
        )
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolHintError(
            f"cannot read artifact: {exc}",
            "confirm the file exists and is UTF-8 text",
        ) from exc
    if section:
        text = _slice_section(text, section, max_bytes)
    return _truncate(text, max_bytes)


@app.tool(
    description=(
        "Query a pruned symbol graph (out/pruned-graph.json): list symbols filtered "
        "by module, kind, or name substring, paginated by offset/limit (limit <= 100). "
        "Returns total, offset, limit, count and a 'symbols' array (id, name, kind, "
        "module, lineno). USE THIS when the user asks about specific symbols ('where "
        "is UserService?') or wants to inspect what is inside a module."
    )
)
def scribe_list_symbols(
    graph_path: str,
    module: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Filter, paginate and return symbols from a serialized symbol graph."""
    if offset < 0:
        raise ToolHintError(
            f"offset must be >= 0, got {offset}", "use offset = 0 to start from the top"
        )
    if limit < 1 or limit > _MAX_SYMBOL_LIMIT:
        raise ToolHintError(
            f"limit must be between 1 and {_MAX_SYMBOL_LIMIT}, got {limit}",
            f"use a limit between 1 and {_MAX_SYMBOL_LIMIT}",
        )
    target = _check_path(graph_path)
    try:
        graph = SymbolGraph.from_json(target.read_text(encoding="utf-8"))
    except (ScribeError, ValidationError, json.JSONDecodeError) as exc:
        raise ToolHintError(
            f"cannot parse symbol graph: {exc}",
            "point graph_path at a pruned-graph.json produced by scribe_graph",
        ) from exc

    nodes = list(graph.nodes.values())
    if module:
        nodes = [n for n in nodes if n.module == module]
    if kind:
        nodes = [n for n in nodes if n.kind.value == kind]
    if q:
        nodes = [n for n in nodes if q in n.name]

    total = len(nodes)
    page = nodes[offset : offset + limit]
    symbols = [
        {
            "id": n.id,
            "name": n.name,
            "kind": n.kind.value,
            "module": n.module,
            "lineno": n.lineno,
        }
        for n in page
    ]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(symbols),
        "symbols": symbols,
    }


async def run_stdio_server() -> None:
    """Run the MCP server over stdio until the client disconnects."""
    await app.run_stdio_async()
