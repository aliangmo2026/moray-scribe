"""Tests for Mermaid sequenceDiagram rendering of sequence flows."""

from __future__ import annotations

import pytest

from moray_scribe.renderer import sequence_flow_to_mermaid, sequence_flow_to_mermaid_fence
from moray_scribe.renderer.mermaid import _escape_sequence_text
from moray_scribe.schema import (
    SequenceFlow,
)


def _flow(**overrides: object) -> SequenceFlow:
    payload: dict[str, object] = {
        "entry": "web.login",
        "title": "用户登录",
        "summary": "从路由进入，校验凭据并回写会话。",
        "participants": [
            {"key": "Web", "label": "Web 前端"},
            {"key": "Auth", "label": "auth 模块"},
            {"key": "DB", "label": "数据库"},
        ],
        "messages": [
            {"sender": "Web", "receiver": "Auth", "label": "POST /login", "kind": "sync"},
            {"sender": "Auth", "receiver": "DB", "label": "SELECT user", "kind": "sync"},
            {"sender": "Auth", "receiver": "Web", "label": "200 OK", "kind": "reply"},
            {"sender": "Auth", "receiver": "Auth", "label": "audit()", "kind": "async"},
        ],
    }
    payload.update(overrides)
    return SequenceFlow.model_validate(payload)


def test_header_and_autonumber() -> None:
    text = sequence_flow_to_mermaid(_flow())

    assert text.startswith("sequenceDiagram\n    autonumber")


def test_participants_render_with_labels() -> None:
    text = sequence_flow_to_mermaid(_flow())

    assert "    participant Web as Web 前端" in text
    assert "    participant Auth as auth 模块" in text


def test_message_arrows_by_kind() -> None:
    text = sequence_flow_to_mermaid(_flow())
    lines = text.splitlines()

    assert '    Web->>Auth: POST /login' in lines
    assert '    Auth-->>Web: 200 OK' in lines
    assert '    Auth-)Auth: audit()' in lines


def test_labels_escaped() -> None:
    text = sequence_flow_to_mermaid(
        _flow(
            messages=[
                {
                    "sender": "Web",
                    "receiver": "Auth",
                    "label": 'a;b\nc&amp;"x"',
                }
            ]
        )
    )

    assert 'a,b c&amp,"x"' in text


def test_escape_sequence_text_rules() -> None:
    assert _escape_sequence_text("a\nb") == "a b"
    assert _escape_sequence_text("a;b") == "a,b"
    assert _escape_sequence_text("100%") == "100%"
    assert _escape_sequence_text("中文;ok") == "中文,ok"
    assert _escape_sequence_text("tab\there") == "tabhere"  # control chars dropped
    assert _escape_sequence_text("nul\x00") == "nul"


def test_deterministic_output() -> None:
    assert sequence_flow_to_mermaid(_flow()) == sequence_flow_to_mermaid(_flow())


def test_fence_wrapping() -> None:
    fence = sequence_flow_to_mermaid_fence(_flow())

    assert fence.startswith("```mermaid\nsequenceDiagram")
    assert fence.endswith("```")


def test_empty_flow_impossible_by_schema() -> None:
    with pytest.raises(ValueError):
        SequenceFlow.model_validate(
            {"entry": "e", "title": "T", "participants": [], "messages": []}
        )
