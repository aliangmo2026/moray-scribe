"""Contracts for LLM-authored business sequence flows (Mermaid sequenceDiagram).

The model never writes Mermaid directly: it returns structured participants and
ordered messages, the engine validates them against this schema, and the
renderer emits the ``sequenceDiagram`` text deterministically. This keeps the
three-layer anti-hallucination stack (schema → prompt whitelist → engine
post-validation) identical to the architecture-draft pipeline.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .architecture import MERMAID_ID_PATTERN
from .base import StrictModel


class SequenceMessageKind(str, Enum):
    """Arrow semantics of one sequence interaction."""

    SYNC = "sync"
    ASYNC = "async"
    REPLY = "reply"


class SequenceParticipant(StrictModel):
    """One swim lane in a sequence diagram."""

    key: str = Field(
        pattern=MERMAID_ID_PATTERN,
        description="Mermaid-safe participant identifier.",
    )
    label: str = Field(
        min_length=1,
        max_length=40,
        description="Display label (may be Chinese).",
    )


def _check_participant_refs(
    participants: list[SequenceParticipant],
    messages: list[SequenceMessage],
) -> None:
    keys = [participant.key for participant in participants]
    duplicates = sorted(key for key in set(keys) if keys.count(key) > 1)
    if duplicates:
        raise ValueError(f"duplicate participant keys: {duplicates}")
    declared = set(keys)
    for index, message in enumerate(messages):
        missing = [
            role
            for role in (message.sender, message.receiver)
            if role not in declared
        ]
        if missing:
            raise ValueError(
                f"message[{index}] references undeclared participants: {missing}"
            )


class SequenceMessage(StrictModel):
    """One ordered interaction between two participants."""

    sender: str = Field(min_length=1, description="Sender participant key.")
    receiver: str = Field(min_length=1, description="Receiver participant key.")
    label: str = Field(
        min_length=1,
        max_length=80,
        description="Message text, e.g. `POST /login` or `verify(token)`.",
    )
    kind: SequenceMessageKind = Field(
        default=SequenceMessageKind.SYNC,
        description="sync `->>`, async `-)`, reply `-->>`.",
    )


class SequenceFlowDraft(StrictModel):
    """Wire shape the model returns for ONE call chain (no entry, no source)."""

    title: str = Field(min_length=1, max_length=80, description="Business flow name.")
    summary: str = Field(default="", max_length=400, description="One-sentence narration.")
    participants: list[SequenceParticipant] = Field(
        min_length=1,
        max_length=8,
        description="Swim lanes, first one usually the entry's module or actor.",
    )
    messages: list[SequenceMessage] = Field(
        min_length=1,
        max_length=24,
        description="Ordered interactions following the traced chain.",
    )

    @model_validator(mode="after")
    def _check_refs(self) -> SequenceFlowDraft:
        _check_participant_refs(self.participants, self.messages)
        return self


class SequencePlan(StrictModel):
    """The model's complete answer: one flow draft per input call chain."""

    flows: list[SequenceFlowDraft] = Field(
        min_length=1,
        max_length=8,
        description="Flows in the same order as the numbered input chains.",
    )


class SequenceFlow(StrictModel):
    """A finalized sequence flow bound to its graph entry point."""

    entry: str = Field(min_length=1, description="Entry symbol id from the traced chain.")
    title: str = Field(min_length=1, max_length=80, description="Business flow name.")
    summary: str = Field(default="", max_length=400, description="One-sentence narration.")
    participants: list[SequenceParticipant] = Field(
        min_length=1,
        max_length=8,
        description="Swim lanes.",
    )
    messages: list[SequenceMessage] = Field(
        min_length=1,
        max_length=24,
        description="Ordered interactions.",
    )
    source: Literal["ai", "engine"] = Field(
        default="ai",
        description="ai = model-authored; engine = deterministic fallback.",
    )

    @model_validator(mode="after")
    def _check_refs(self) -> SequenceFlow:
        _check_participant_refs(self.participants, self.messages)
        return self
