"""Tests for the concurrency-capped async Ollama client (backend faked)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from moray_scribe.describer.async_client import AsyncOllamaClient
from moray_scribe.exceptions import DescriberError
from moray_scribe.schema import StrictModel


class _Box(StrictModel):
    value: int


class _FakeAsyncBackend:
    """Stand-in for ``ollama.AsyncClient`` recording calls and replaying answers."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.call_log: list[dict[str, Any]] = []
        self.active = 0
        self.max_active = 0

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.call_log.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if not self.responses:
                raise StopIteration("no more fake responses")
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return {
                "message": {"content": item},
                "prompt_eval_count": 100,
                "eval_count": 20,
            }
        finally:
            self.active -= 1


def _make_client(
    responses: list[Any], concurrency: int = 4
) -> tuple[AsyncOllamaClient, _FakeAsyncBackend]:
    client = AsyncOllamaClient(host="http://localhost:11434", concurrency=concurrency)
    backend = _FakeAsyncBackend(responses)
    client._client = backend  # type: ignore[attr-defined]
    return client, backend


def test_chat_json_parses_valid_response() -> None:
    client, backend = _make_client(['{"value": 1}'])

    result = asyncio.run(client.chat_json("m", "sys", "user", _Box))

    assert result == _Box(value=1)
    assert client.requests == 1
    assert client.prompt_tokens == 100
    assert client.completion_tokens == 20
    assert backend.call_log[0]["format"] == "json"


def test_chat_json_retries_invalid_then_succeeds() -> None:
    client, backend = _make_client(["not json", '{"value": 2}'])

    result = asyncio.run(client.chat_json("m", "sys", "user", _Box, retries=1))

    assert result == _Box(value=2)
    assert len(backend.call_log) == 2
    # The retry embeds the correction block.
    assert "Correction required" in backend.call_log[1]["messages"][0]["content"]


def test_chat_json_exhausts_retries() -> None:
    client, _backend = _make_client(["bad", "still bad"])

    with pytest.raises(DescriberError, match="async chat_json failed"):
        asyncio.run(client.chat_json("m", "sys", "user", _Box, retries=1))


def test_transport_unreachable_classified() -> None:
    client, _backend = _make_client([ConnectionError("connection refused")])

    with pytest.raises(DescriberError, match="not reachable"):
        asyncio.run(client.chat_json("m", "sys", "user", _Box))


def test_concurrency_capped() -> None:
    responses = ['{"value": 1}'] * 8
    client, backend = _make_client(responses, concurrency=2)

    async def _run() -> None:
        await asyncio.gather(
            *(client.chat_json("m", "sys", "user", _Box) for _ in range(8))
        )

    asyncio.run(_run())

    assert backend.max_active <= 2
    assert client.requests == 8
    assert client.prompt_tokens == 800


def test_num_ctx_bounded() -> None:
    client, _backend = _make_client([])
    small = client._num_ctx("s", "u", max_tokens=64)
    large = client._num_ctx("x" * 200000, "y" * 200000, max_tokens=4096)

    assert small == 4096
    assert large == 16384
