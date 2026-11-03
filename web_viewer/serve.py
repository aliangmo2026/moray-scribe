"""Dependency-free static server for the moray-scribe web viewer.

Usage::

    python web_viewer/serve.py out/pruned-graph.json --port 8765

The server exposes the viewer assets plus three JSON endpoints:

- ``GET /api/config``           project metadata (name, graph path, source root)
- ``GET /api/graph``            the graph JSON exactly as moray-scribe wrote it
- ``GET /api/source?file=&start=&end=``  a source slice for the side panel

Only Python's standard library is used, so the viewer runs anywhere the CLI
runs (no npm, no CDN, no network).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ASSET_DIR = Path(__file__).resolve().parent
_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}

# Guard rails for the source endpoint: a view is a few dozen lines at most.
_MAX_SOURCE_LINES = 400


class ViewerState:
    """Everything the HTTP handler needs, shared across threads (read-only)."""

    def __init__(self, graph_path: Path, source_root: Path | None, project: str | None) -> None:
        self.graph_path = graph_path
        self.graph_text = graph_path.read_text(encoding="utf-8")
        metadata = _graph_metadata(self.graph_text)
        # The pipeline stamps the scan root into the graph, so the viewer can
        # resolve source files without extra flags. A relative stamp (used by
        # committed demo graphs) is resolved against the graph file's folder.
        stamped = metadata.get("root")
        root_candidate: Path | None = None
        if stamped:
            root_candidate = Path(stamped)
            if not root_candidate.is_absolute():
                root_candidate = graph_path.parent / root_candidate
        resolved_root = source_root or (
            root_candidate if root_candidate and root_candidate.is_dir() else graph_path.parent
        )
        self.source_root = resolved_root.resolve()
        self.project = project or metadata.get("project") or self.source_root.name
        self.nodes = _count_nodes(self.graph_text)

    def source_slice(self, relative: str, start: int, end: int) -> list[str]:
        """Return source lines ``[start, end]`` (1-based, inclusive).

        Raises ``ValueError`` when the path escapes the source root or cannot
        be read; the handler turns that into a 400/404.
        """
        candidate = (self.source_root / relative).resolve()
        root = str(self.source_root)
        if candidate != self.source_root and not str(candidate).startswith(root + os.sep):
            raise ValueError("path escapes the source root")
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start)
        end = min(len(lines), max(start, end))
        if end - start + 1 > _MAX_SOURCE_LINES:
            end = start + _MAX_SOURCE_LINES - 1
        width = len(str(end))
        return [f"{number:>{width}} | {lines[number - 1]}" for number in range(start, end + 1)]


def _handler_factory(state: ViewerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "moray-scribe-viewer/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[viewer] " + fmt % args + "\n")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path in _ASSETS:
                name, content_type = _ASSETS[path]
                try:
                    body = (ASSET_DIR / name).read_bytes()
                except OSError:
                    self._json({"error": f"missing asset {name}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._send(HTTPStatus.OK, body, content_type)
                return

            if path == "/api/config":
                self._json(
                    {
                        "project": state.project,
                        "graph": state.graph_path.name,
                        "source_root": str(state.source_root),
                        "nodes": state.nodes,
                    }
                )
                return

            if path == "/api/graph":
                self._send(HTTPStatus.OK, state.graph_text.encode("utf-8"),
                           "application/json; charset=utf-8")
                return

            if path == "/api/source":
                query = parse_qs(parsed.query)
                relative = unquote(query.get("file", [""])[0])
                try:
                    start = int(query.get("start", ["1"])[0])
                    end = int(query.get("end", ["80"])[0])
                except ValueError:
                    self._json({"error": "start/end must be integers"}, HTTPStatus.BAD_REQUEST)
                    return
                if not relative:
                    self._json({"error": "file is required"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    lines = state.source_slice(relative, start, end)
                except ValueError as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                except (OSError, UnicodeDecodeError):
                    self._json({"error": f"cannot read {relative}"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"file": relative, "start": start, "lines": lines})
                return

            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def _count_nodes(graph_text: str) -> int:
    try:
        payload = json.loads(graph_text)
    except json.JSONDecodeError:
        return 0
    nodes = payload.get("nodes")
    return len(nodes) if isinstance(nodes, dict) else 0


def _graph_metadata(graph_text: str) -> dict[str, str]:
    """Read the graph's ``metadata`` map, tolerating a malformed payload."""
    try:
        payload = json.loads(graph_text)
    except json.JSONDecodeError:
        return {}
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the moray-scribe web viewer.")
    parser.add_argument("graph", type=Path, help="Path to pruned-graph.json.")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1).")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="Project root used to resolve source files (default: graph's parent).",
    )
    parser.add_argument("--project", default=None, help="Display name (default: graph folder).")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser.")
    args = parser.parse_args(argv)

    graph_path = args.graph.resolve()
    if not graph_path.is_file():
        parser.error(f"graph not found: {graph_path}")
    source_root = (args.source_root or graph_path.parent).resolve()
    project = args.project

    state = ViewerState(graph_path, source_root if args.source_root else None, project)
    httpd = ThreadingHTTPServer((args.host, args.port), _handler_factory(state))
    url = f"http://{args.host}:{args.port}/"
    print(f"moray-scribe viewer: {url}")
    print(f"  graph:  {graph_path}")
    print(f"  source: {state.source_root}")
    if not args.no_browser:
        # Headless / server environments may have no browser to open.
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
