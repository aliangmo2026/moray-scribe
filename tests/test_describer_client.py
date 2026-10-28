"""Unit tests for the schema-validating Ollama client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import ollama
import pytest

from moray_scribe.describer import OllamaClient
from moray_scribe.exceptions import DescriberError
from moray_scribe.schema import ArchitectureDraft

_LONG = "This summary is deliberately long enough to clear the fifty character minimum."


def _valid_draft() -> str:
    return json.dumps(
        {
            "title": "Arch",
            "summary": _LONG,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Overview",
                    "nodes": [{"id": "Core", "label": "Core", "kind": "module"}],
                    "edges": [],
                }
            ],
            "modules": [{"module": "core", "summary": "Core.", "symbol_ids": ["core.Engine"]}],
        }
    )


def _chat(client: OllamaClient, schema: Any = ArchitectureDraft, **kwargs: Any) -> Any:
    return client.chat_json(
        model="test-model",
        system="SYS",
        user="USR",
        schema=schema,
        **kwargs,
    )


def test_success_first_try(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([_valid_draft()])

    obj = _chat(client)

    assert obj.title == "Arch"
    assert len(client.backend.call_log) == 1


def test_json_syntax_error_retries(fake_ollama_client: Any) -> None:
    client = fake_ollama_client(["{invalid", _valid_draft()])

    obj = _chat(client, retries=2)

    assert obj.title == "Arch"
    assert len(client.backend.call_log) == 2


def test_schema_validation_error_retries(fake_ollama_client: Any) -> None:
    missing_title = json.dumps({"summary": _LONG})
    client = fake_ollama_client([missing_title, _valid_draft()])

    obj = _chat(client, retries=2)

    assert obj.title == "Arch"
    assert len(client.backend.call_log) == 2


def test_dangling_edge_retries(fake_ollama_client: Any) -> None:
    dangling = json.dumps(
        {
            "title": "Arch",
            "summary": _LONG,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Overview",
                    "nodes": [{"id": "Core", "label": "Core"}],
                    "edges": [{"source": "Core", "target": "Ghost"}],
                }
            ],
        }
    )
    client = fake_ollama_client([dangling, _valid_draft()])

    obj = _chat(client, retries=2)

    assert obj.title == "Arch"
    second = client.backend.call_log[1]
    system_text = second["messages"][0]["content"]
    assert "Valid constraints" in system_text


def test_validator_failure_retries(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([_valid_draft(), _valid_draft()])
    seen: list[int] = []

    def validator(draft: ArchitectureDraft) -> list[str]:
        seen.append(1)
        return ["semantic problem"] if len(seen) == 1 else []

    obj = _chat(client, retries=2, validator=validator)

    assert obj.title == "Arch"
    assert len(client.backend.call_log) == 2


def test_exhausted_retries_raises(fake_ollama_client: Any) -> None:
    bad = "{invalid"
    client = fake_ollama_client([bad, bad, bad])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client, retries=2)

    message = str(excinfo.value)
    assert "last output" in message
    assert bad in message
    assert len(client.backend.call_log) == 3


def test_ollama_unreachable_raises(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([ConnectionError("refused")])

    with pytest.raises(DescriberError, match="not reachable"):
        _chat(client)


def test_read_timeout_has_actionable_hint(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([httpx.ReadTimeout("timed out")])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client)

    message = str(excinfo.value)
    assert "timed out" in message
    assert "hint:" in message
    assert "MORAY_SCRIBE_REQUEST_TIMEOUT_S=900" in message
    assert "MORAY_SCRIBE_MAX_OUTPUT_TOKENS=2048" in message
    assert "qwen2.5:1.5b" in message
    assert "--max-nodes" in message
    # A timeout is NOT "unreachable": the service answered, it was just slow.
    assert "not reachable" not in message
    assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)


def test_connect_error_has_start_ollama_hint(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([httpx.ConnectError("connection refused")])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client)

    message = str(excinfo.value)
    assert "not reachable" in message
    assert "ollama serve" in message
    assert "MORAY_SCRIBE_OLLAMA_HOST" in message
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


def test_http_status_error_mentions_model(fake_ollama_client: Any) -> None:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    response = httpx.Response(404, request=request, text="model 'ghost' not found")
    error = httpx.HTTPStatusError("not found", request=request, response=response)
    client = fake_ollama_client([error])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client)

    message = str(excinfo.value)
    assert "HTTP 404" in message
    assert "test-model" in message
    assert "ollama list" in message
    assert "ollama pull test-model" in message


def test_translated_response_error_still_mentions_model(fake_ollama_client: Any) -> None:
    """The ollama client translates HTTPStatusError to ResponseError; same advice."""
    client = fake_ollama_client([ollama.ResponseError("model 'ghost' not found", 404)])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client)

    message = str(excinfo.value)
    assert "HTTP 404" in message
    assert "test-model" in message
    assert "ollama list" in message


def test_unknown_exception_retains_repr(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([ValueError("foo")])

    with pytest.raises(DescriberError) as excinfo:
        _chat(client)

    message = str(excinfo.value)
    assert "Unexpected error calling Ollama" in message
    assert "foo" in message  # original repr is not swallowed
    assert "hint:" in message
    assert "moray-scribe health" in message


def test_call_log_records_prompts(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([_valid_draft()])

    _chat(client)

    call = client.backend.call_log[0]
    assert call["model"] == "test-model"
    assert call["format"] == "json"
    assert call["messages"][0] == {"role": "system", "content": "SYS"}
    assert call["messages"][1] == {"role": "user", "content": "USR"}


def test_client_extra_forbidden_mentions_allowed_fields(fake_ollama_client: Any) -> None:
    bad = json.dumps(
        {
            "title": "T",
            "summary": _LONG,
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "d",
                    "nodes": [{"id": "A", "label": "A", "dependencies": []}],
                    "edges": [],
                }
            ],
        }
    )
    client = fake_ollama_client([bad, _valid_draft()])

    obj = _chat(client, retries=2)

    assert obj.title == "Arch"
    system_text = client.backend.call_log[1]["messages"][0]["content"]
    assert "`MermaidNode` accepts ONLY these fields" in system_text
    assert "dependencies" in system_text
    assert "is not allowed" in system_text


def test_client_max_tokens_passed(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([_valid_draft()])

    _chat(client, max_tokens=1234)

    assert client.backend.call_log[0]["options"]["num_predict"] == 1234


def test_client_default_max_tokens(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([_valid_draft()])

    _chat(client)

    assert client.backend.call_log[0]["options"]["num_predict"] == 4096


def test_client_extra_forbidden_top_level_model(fake_ollama_client: Any) -> None:
    bad = json.dumps({"title": "T", "summary": _LONG, "oops": 1})
    client = fake_ollama_client([bad, _valid_draft()])

    obj = _chat(client, retries=2)

    assert obj.title == "Arch"
    system_text = client.backend.call_log[1]["messages"][0]["content"]
    assert "`ArchitectureDraft` accepts ONLY these fields" in system_text
