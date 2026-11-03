"""Tests for the offline mock-Ollama ecosystem (replay recordings + guard).

The suite must pass with NO real Ollama running: the autouse offline guard
fails any test that tries to connect to the real service port, and the
``mock_ollama`` fixture replays wire-shaped responses captured from real
model runs (benchmarks/capture_recordings.py).
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from moray_scribe.describer import AsyncOllamaClient
from moray_scribe.schema import SequencePlan
from tests.mock_ollama import RECORDINGS_DIR, OfflineNetworkError


def test_recordings_dir_exists_and_is_populated() -> None:
    files = sorted(RECORDINGS_DIR.rglob("*.json"))
    assert files, "no recordings captured; run benchmarks/capture_recordings.py"


def test_every_recording_is_wellformed() -> None:
    for path in sorted(RECORDINGS_DIR.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["endpoint"] == "/api/chat", path
        content = record["response"]["message"]["content"]
        assert isinstance(content, str) and content.strip(), path
        assert record["model"], path
        assert "captured_at" in record, path


def test_mock_ollama_serves_replay_over_real_http(mock_ollama) -> None:
    """The fixture is a real localhost HTTP server speaking the Ollama wire."""
    client = mock_ollama.client()
    reply = client.chat_json(
        "recorded-model",
        "system",
        mock_ollama.user_prompt_of("sequence-plan"),
        SequencePlan,
    )

    assert isinstance(reply, SequencePlan)
    assert reply.flows
    assert mock_ollama.requests >= 1
    assert mock_ollama.request_bodies[0]["model"] == "recorded-model"


def test_mock_ollama_replays_describer_draft_json(mock_ollama) -> None:
    """The describer recording is REAL model output that parses as a draft."""
    import json as jsonlib

    mock_ollama.enqueue(["describer-draft"])
    client = mock_ollama.client()
    raw = client._chat_once("recorded-model", "system", "user", 0.1, 512)
    payload = jsonlib.loads(raw)

    assert payload["title"]
    assert isinstance(payload["diagrams"], list)


def test_async_client_replays_against_mock(mock_ollama) -> None:
    import asyncio

    async def _run():
        mock_ollama.enqueue(["sequence-plan"])
        client = AsyncOllamaClient(host=mock_ollama.host, timeout_s=30)
        return await client.chat_json(
            "recorded-model",
            "system",
            mock_ollama.user_prompt_of("sequence-plan"),
            SequencePlan,
            max_tokens=512,
        )

    plan = asyncio.run(_run())

    assert plan.flows


def test_web_viewer_chat_backend_replays(mock_ollama) -> None:
    from web_viewer.chat import OllamaChatBackend

    backend = OllamaChatBackend(host=mock_ollama.host, model="recorded-model", timeout_s=30)
    result = backend.answer("修改 login 会影响什么？", {"nodes": [], "edges": [], "slices": []})

    assert result["answer"]
    assert result["degraded"] is False


def test_fifo_replay_order(mock_ollama) -> None:
    names = mock_ollama.recording_names()
    mock_ollama.enqueue(["chat-answer", "describer-draft"])

    first = mock_ollama.client()._chat_once("m", "s", "u", 0.1, 64)
    second = mock_ollama.client()._chat_once("m", "s", "u", 0.1, 64)

    assert first == mock_ollama.raw_response_of("chat-answer")
    assert second == mock_ollama.raw_response_of("describer-draft")
    assert "chat-answer" in names  # names API stable


def test_tags_endpoint_lists_recorded_models(mock_ollama) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{mock_ollama.host}/api/tags", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert payload["models"]
    assert all("name" in model for model in payload["models"])


def test_offline_guard_blocks_real_ollama_port() -> None:
    """Connecting to a real Ollama port inside tests must fail loudly."""
    with pytest.raises(OfflineNetworkError):
        socket.create_connection(("127.0.0.1", 11434), timeout=1)


def test_offline_guard_allows_local_test_servers() -> None:
    """Ephemeral loopback servers (the mock itself) stay reachable."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sock = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=2)
        sock.close()
    finally:
        server.shutdown()


def test_missing_recording_name_is_clear(mock_ollama) -> None:
    with pytest.raises(KeyError):
        mock_ollama.raw_response_of("no-such-recording")
