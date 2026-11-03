"""Tests for the /api/chat endpoint and its Ollama chat backend.

Loopback-only HTTP tests use ``http.client`` against an ephemeral
``127.0.0.1`` server, so nothing here can reach an unintended host.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from web_viewer.chat import CHAT_SYSTEM_PROMPT, ChatError, OllamaChatBackend, render_user_prompt
from web_viewer.retrieval import RetrievalIndex, select_subgraph
from web_viewer.serve import ViewerState, _handler_factory

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _write_graph(path: Path, *, source: Path | None = None) -> Path:
    nodes: dict[str, dict[str, Any]] = {
        "app.login_route": {
            "id": "app.login_route",
            "name": "login_route",
            "kind": "function",
            "module": "app",
            "file": "app.py",
            "lineno": 1,
            "end_lineno": 4,
            "pagerank": 0.4,
            "route": {"path": "/login", "methods": ["POST"]},
        },
        "app.check_password": {
            "id": "app.check_password",
            "name": "check_password",
            "kind": "function",
            "model": "app",  # typo tolerated by the index
            "module": "app",
            "file": "app.py",
            "lineno": 6,
            "end_lineno": 9,
            "pagerank": 0.3,
        },
        "db.fetch_user": {
            "id": "db.fetch_user",
            "name": "fetch_user",
            "kind": "function",
            "module": "db",
            "file": "db.py",
            "lineno": 1,
            "end_lineno": 3,
            "pagerank": 0.2,
        },
    }
    payload = {
        "nodes": nodes,
        "edges": [
            {"source": "app.login_route", "target": "app.check_password", "kind": "calls"},
            {"source": "app.check_password", "target": "db.fetch_user", "kind": "calls"},
        ],
        "metadata": {"project": "demo"},
    }
    if source is not None:
        payload["metadata"]["root"] = str(source)  # type: ignore[index]
    target = path / "pruned-graph.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


class _FakeChatBackend:
    """Records questions and replays a canned answer."""

    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.questions: list[str] = []

    def answer(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        self.questions.append(question)
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response)


@pytest.fixture()
def viewer(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "project"
    source.mkdir()
    (source / "app.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 21)), encoding="utf-8"
    )
    graph = _write_graph(tmp_path, source=source)
    return graph, source


def _serve(state: ViewerState) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(url: str, payload: object) -> tuple[int, dict[str, Any]]:
    """POST JSON to the loopback test server and return (status, body)."""
    prefix = "http://"
    assert url.startswith(prefix)
    rest = url[len(prefix):]
    host, _, port = rest.partition(":")
    connection = HTTPConnection(host, int(port), timeout=5)
    try:
        connection.request(
            "POST",
            "/api/chat",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)
    finally:
        connection.close()


def _make_state(graph: Path, backend: Any) -> ViewerState:
    """A ViewerState with the chat seam injected (keeps the real constructor)."""
    state = ViewerState(graph, None, None)
    state.chat_backend = backend  # type: ignore[attr-defined]
    return state


# --------------------------------------------------------------------------
# HTTP endpoint
# --------------------------------------------------------------------------


def test_chat_endpoint_happy_path(viewer: tuple[Path, Path]) -> None:
    graph, _source = viewer
    backend = _FakeChatBackend({"answer": "会影响 db.fetch_user", "nodes": ["db.fetch_user"]})
    state = _make_state(graph, backend)
    server, url = _serve(state)
    try:
        status, payload = _post(url, {"question": "修改 login 逻辑会影响什么？"})
    finally:
        server.shutdown()

    assert status == HTTPStatus.OK
    assert payload["answer"] == "会影响 db.fetch_user"
    assert payload["nodes"] == ["db.fetch_user"]
    assert payload["context"]["nodes"] >= 1
    assert backend.questions == ["修改 login 逻辑会影响什么？"]


def test_chat_endpoint_invalid_json(viewer: tuple[Path, Path]) -> None:
    graph, _source = viewer
    state = _make_state(graph, _FakeChatBackend({"answer": "x", "nodes": []}))
    server, _url = _serve(state)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request("POST", "/api/chat", body=b"not json")
            response = connection.getresponse()
            assert response.status == HTTPStatus.BAD_REQUEST
        finally:
            connection.close()
    finally:
        server.shutdown()


def test_chat_endpoint_missing_question(viewer: tuple[Path, Path]) -> None:
    graph, _source = viewer
    state = _make_state(graph, _FakeChatBackend({"answer": "x", "nodes": []}))
    server, url = _serve(state)
    try:
        status, payload = _post(url, {"question": "   "})
    finally:
        server.shutdown()

    assert status == HTTPStatus.BAD_REQUEST
    assert "question" in payload["error"]


def test_chat_endpoint_disabled(viewer: tuple[Path, Path]) -> None:
    graph, _source = viewer
    state = _make_state(graph, None)
    server, url = _serve(state)
    try:
        status, payload = _post(url, {"question": "hi"})
    finally:
        server.shutdown()

    assert status == HTTPStatus.NOT_IMPLEMENTED
    assert "chat" in payload["error"]


def test_chat_endpoint_backend_error_maps_to_502(viewer: tuple[Path, Path]) -> None:
    graph, _source = viewer
    state = _make_state(graph, _FakeChatBackend(ChatError("Ollama unreachable")))
    server, url = _serve(state)
    try:
        status, payload = _post(url, {"question": "hi"})
    finally:
        server.shutdown()

    assert status == HTTPStatus.BAD_GATEWAY
    assert "Ollama unreachable" in payload["error"]


# --------------------------------------------------------------------------
# OllamaChatBackend
# --------------------------------------------------------------------------


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    body = b'{"message": {"content": "{}"}}'

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def _serve_fake_ollama(body: bytes) -> tuple[ThreadingHTTPServer, str]:
    _FakeOllamaHandler.body = body
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_ollama_backend_parses_filters_and_caps_nodes() -> None:
    cited = [f"m.f{index}" for index in range(12)] + ["ghost.node"]
    content = json.dumps({"answer": "影响面如下", "nodes": cited})
    server, host = _serve_fake_ollama(
        json.dumps({"message": {"content": content}}).encode("utf-8")
    )
    try:
        backend = OllamaChatBackend(host=host, model="test-model", timeout_s=10)
        context = {
            "nodes": [{"id": f"m.f{index}"} for index in range(12)],
            "edges": [],
            "slices": [],
        }
        result = backend.answer("问题", context)
    finally:
        server.shutdown()

    assert result["answer"] == "影响面如下"
    # Ghost ids dropped, capped at 8.
    assert result["nodes"] == [f"m.f{index}" for index in range(8)]
    assert result["degraded"] is False


def test_ollama_backend_degrades_on_prose_answer() -> None:
    server, host = _serve_fake_ollama(
        json.dumps({"message": {"content": "这不是 JSON，但我还是想回答"}}).encode("utf-8")
    )
    try:
        backend = OllamaChatBackend(host=host, model="m", timeout_s=10)
        result = backend.answer("q", {"nodes": [], "edges": [], "slices": []})
    finally:
        server.shutdown()

    assert result["answer"] == "这不是 JSON，但我还是想回答"
    assert result["nodes"] == []
    assert result["degraded"] is True


def test_ollama_backend_rejects_non_http_scheme() -> None:
    with pytest.raises(ChatError, match="http"):
        OllamaChatBackend(host="file:///etc/passwd", model="m", timeout_s=2)

    with pytest.raises(ChatError, match="http"):
        OllamaChatBackend(host="", model="m", timeout_s=2)


def test_ollama_backend_refused_connection_raises_chat_error() -> None:
    backend = OllamaChatBackend(host="http://127.0.0.1:1", model="m", timeout_s=2)

    with pytest.raises(ChatError):
        backend.answer("q", {"nodes": [], "edges": [], "slices": []})


def test_render_user_prompt_contains_context_and_question() -> None:
    index = RetrievalIndex(
        {
            "nodes": {
                "app.login": {
                    "id": "app.login",
                    "name": "login",
                    "kind": "function",
                    "module": "app",
                    "file": "app.py",
                    "pagerank": 0.5,
                }
            },
            "edges": [],
        },
        source_root=None,
    )
    subgraph = select_subgraph(index, "login")
    prompt = render_user_prompt("影响哪些模块？", subgraph.to_context(), project="demo")

    assert "app.login" in prompt
    assert "影响哪些模块？" in prompt
    assert "NODES" in prompt.upper()


def test_system_prompt_demands_json_and_grounding() -> None:
    assert "JSON" in CHAT_SYSTEM_PROMPT
    assert "nodes" in CHAT_SYSTEM_PROMPT
    assert "graph" in CHAT_SYSTEM_PROMPT.lower()
