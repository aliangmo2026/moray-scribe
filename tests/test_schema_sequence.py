"""Tests for the sequence-diagram schema contracts."""

from __future__ import annotations

import pytest

from moray_scribe.schema import (
    SequenceFlow,
    SequenceFlowDraft,
    SequenceMessage,
    SequenceMessageKind,
    SequenceParticipant,
    SequencePlan,
)


def _participant(key: str, label: str) -> SequenceParticipant:
    return SequenceParticipant(key=key, label=label)


def _msg(sender: str, receiver: str, label: str = "do()") -> SequenceMessage:
    return SequenceMessage(sender=sender, receiver=receiver, label=label)


def _flow(**overrides: object) -> SequenceFlow:
    payload: dict[str, object] = {
        "entry": "app.main",
        "title": "登录流程",
        "summary": "从路由到数据库的登录调用链。",
        "participants": [_participant("Web", "Web 前端"), _participant("API", "后端 API")],
        "messages": [_msg("Web", "API")],
    }
    payload.update(overrides)
    return SequenceFlow.model_validate(payload)


def test_flow_holds_entry_title_participants_messages() -> None:
    flow = _flow()

    assert flow.entry == "app.main"
    assert flow.source == "ai"
    assert flow.participants[0].key == "Web"
    assert flow.messages[0].kind is SequenceMessageKind.SYNC


def test_participant_key_must_be_mermaid_safe() -> None:
    with pytest.raises(ValueError):
        SequenceParticipant(key="bad key!", label="x")


def test_message_kind_enum() -> None:
    assert SequenceMessageKind("sync") is SequenceMessageKind.SYNC
    assert SequenceMessageKind("reply").value == "reply"
    assert SequenceMessageKind("async").value == "async"


def test_flow_rejects_undeclared_participants() -> None:
    with pytest.raises(ValueError, match="undeclared"):
        SequenceFlow.model_validate(
            {
                "entry": "app.main",
                "title": "T",
                "participants": [{"key": "Web", "label": "前端"}],
                "messages": [{"sender": "Web", "receiver": "Ghost", "label": "x"}],
            }
        )


def test_flow_rejects_duplicate_participant_keys() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _flow(
            participants=[_participant("Web", "A"), _participant("Web", "B")],
        )


def test_flow_requires_at_least_one_message_and_participant() -> None:
    with pytest.raises(ValueError):
        _flow(messages=[])
    with pytest.raises(ValueError):
        _flow(participants=[], messages=[_msg("Web", "API")])


def test_draft_plan_is_wire_shaped() -> None:
    draft = SequenceFlowDraft.model_validate(
        {
            "title": "下单流程",
            "participants": [
                {"key": "API", "label": "后端"},
                {"key": "DB", "label": "数据库"},
            ],
            "messages": [{"sender": "API", "receiver": "DB", "label": "INSERT", "kind": "sync"}],
        }
    )
    plan = SequencePlan(flows=[draft])

    assert plan.flows[0].title == "下单流程"
    assert plan.flows[0].messages[0].receiver == "DB"


def test_plan_bounds_flow_count() -> None:
    drafts = [
        {
            "title": f"流程{i}",
            "participants": [{"key": "A", "label": "a"}],
            "messages": [{"sender": "A", "receiver": "A", "label": "x"}],
        }
        for i in range(9)
    ]
    with pytest.raises(ValueError):
        SequencePlan.model_validate({"flows": drafts})


def test_self_message_is_allowed() -> None:
    flow = _flow(messages=[_msg("API", "API", "校验自身")])

    assert flow.messages[0].sender == flow.messages[0].receiver
