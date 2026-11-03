"""Offline mock-Ollama ecosystem for the test suite.

Two pieces:

1. **Replay server** (:class:`MockOllama`) — a real localhost HTTP server that
   speaks the Ollama wire protocol (``POST /api/chat``, ``GET /api/tags``) and
   replays responses *captured from real model runs*
   (``benchmarks/capture_recordings.py`` → ``tests/fixtures/recordings/``).
   Any consumer that talks HTTP — the sync client, the async client, the web
   viewer's chat backend — runs against it unchanged.

2. **Offline guard** (:func:`install_offline_guard`, installed autouse by
   ``conftest``) — any attempt to connect to a real Ollama port (11434) inside
   a test raises :class:`OfflineNetworkError`, so the suite can never quietly
   depend on a local service. Loopback test servers on ephemeral ports stay
   reachable.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RECORDINGS_DIR = Path(__file__).resolve().parent / "fixtures" / "recordings"

# The real-service port the guard blocks inside tests.
OFFLINE_PORT = 11434

_GUARD_INSTALLED = False


class OfflineNetworkError(ConnectionError):
    """Raised when a test tries to reach a real Ollama port.

    Tests must consume :func:`mock_ollama` (the replay server) instead — the
    suite is required to pass on machines with no Ollama installed at all.
    """


def _is_ollama_address(address: object) -> bool:
    return (
        isinstance(address, tuple) and len(address) >= 2 and address[1] == OFFLINE_PORT
    )


def install_offline_guard() -> None:
    """Patch socket APIs so connects to the real Ollama port fail loudly.

    Idempotent: once installed for the pytest session, later calls are no-ops
    (re-installation would wrap the guard around itself).
    """
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def guarded_connect(sock_self: socket.socket, address: object) -> Any:
        if _is_ollama_address(address):
            raise OfflineNetworkError(
                f"test tried to connect to the real Ollama at {address!r}; "
                "use the mock_ollama fixture (replay server) instead"
            )
        return original_connect(sock_self, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if _is_ollama_address(address):
            raise OfflineNetworkError(
                f"test tried to connect to the real Ollama at {address!r}; "
                "use the mock_ollama fixture (replay server) instead"
            )
        return original_create_connection(address, *args, **kwargs)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    _GUARD_INSTALLED = True


def load_recordings(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load every ``*.json`` capture into ``{name: record}`` (sorted by name)."""
    root = directory if directory is not None else RECORDINGS_DIR
    recordings: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return recordings
    for path in sorted(root.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        recordings[path.stem] = record
    return recordings


class _ReplayState:
    """Shared, thread-safe state for the replay handler."""

    def __init__(self, queue: list[dict[str, Any]]) -> None:
        self._lock = threading.Lock()
        self.queue = queue
        self.requests = 0
        self.request_bodies: list[dict[str, Any]] = []

    def next_response(self, request_body: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self.requests += 1
            self.request_bodies.append(request_body)
            if self.queue:
                return self.queue.pop(0)
            return None


def _handler_factory(state: _ReplayState) -> type[BaseHTTPRequestHandler]:
    class ReplayHandler(BaseHTTPRequestHandler):
        server_version = "moray-mock-ollama/1.0"

        def log_message(self, fmt: str, *args: object) -> None:
            pass  # keep test output clean

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/chat":
                self._send(404, {"error": f"unknown path {self.path}"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                request_body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                request_body = {}
            response = state.next_response(request_body)
            if response is None:
                self._send(500, {"error": "replay queue empty (enqueue more recordings)"})
                return
            self._send(200, response)

        def do_GET(self) -> None:
            if self.path == "/api/tags":
                models = sorted(
                    {str(record.get("model") or "recorded-model") for record in state.queue}
                )
                self._send(
                    200,
                    {"models": [{"name": name, "model": name} for name in models]},
                )
                return
            self._send(404, {"error": f"unknown path {self.path}"})

    return ReplayHandler


class MockOllama:
    """A localhost Ollama-compatible server replaying real captured responses."""

    def __init__(
        self,
        recordings: dict[str, dict[str, Any]],
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.recordings = recordings
        initial_queue = [record["response"] for record in recordings.values()]
        self._state = _ReplayState(list(initial_queue))
        self._server = ThreadingHTTPServer((host, port), _handler_factory(self._state))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @classmethod
    def load(cls, directory: Path | None = None) -> MockOllama:
        return cls(load_recordings(directory))

    @property
    def host(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> int:
        return self._state.requests

    @property
    def request_bodies(self) -> list[dict[str, Any]]:
        return list(self._state.request_bodies)

    def recording_names(self) -> list[str]:
        return list(self.recordings)

    def raw_response_of(self, name: str) -> str:
        return str(self.recordings[name]["response"]["message"]["content"])

    def user_prompt_of(self, name: str) -> str:
        return str(self.recordings[name]["prompt"]["user"])

    def system_prompt_of(self, name: str) -> str:
        return str(self.recordings[name]["prompt"]["system"])

    def enqueue(self, names: list[str]) -> None:
        """Reset the replay queue to the given recording order."""
        missing = [name for name in names if name not in self.recordings]
        if missing:
            raise KeyError(f"unknown recordings: {missing}")
        with self._state._lock:
            self._state.queue = [self.recordings[name]["response"] for name in names]

    def client(self) -> Any:
        """A real sync :class:`OllamaClient` pointed at the replay server."""
        from moray_scribe.describer import OllamaClient

        return OllamaClient(host=self.host, timeout_s=30)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
