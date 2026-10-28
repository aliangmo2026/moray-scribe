"""Architecture schema boundary tests."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from moray_scribe.schema import ArchitectureDraft, MermaidNode

VALID_DRAFT: dict[str, Any] = {
    "title": "Sample Architecture",
    "summary": "A two-module sample used to prove the schema boundary.",
    "diagrams": [
        {
            "kind": "flowchart",
            "direction": "TD",
            "title": "Runtime view",
            "nodes": [
                {
                    "id": "api",
                    "label": "API",
                    "kind": "service",
                    "description": "HTTP edge",
                },
                {
                    "id": "core",
                    "label": "Core",
                    "kind": "module",
                    "module": "sample.core",
                    "symbol_ids": ["sample.core.Engine"],
                },
            ],
            "edges": [
                {
                    "source": "api",
                    "target": "core",
                    "label": "calls",
                    "kind": "calls",
                    "style": "solid",
                }
            ],
        }
    ],
    "modules": [
        {
            "module": "sample.core",
            "file": "core.py",
            "summary": "Domain types.",
            "responsibilities": ["Hold the engine"],
            "symbol_ids": ["sample.core.Engine"],
            "dependencies": [],
            "risk": "medium",
        }
    ],
    "core_hubs": ["sample.core.Engine"],
    "dead_code": [],
    "risks": ["No persistence layer."],
    "recommendations": ["Add regression tests."],
    "metadata": {"model": "qwen2.5-coder:14b"},
}


def _validate(payload: dict[str, Any]) -> ArchitectureDraft:
    return ArchitectureDraft.model_validate_json(json.dumps(payload))


def test_valid_draft_validates_from_json() -> None:
    draft = _validate(VALID_DRAFT)

    assert draft.title == "Sample Architecture"
    assert draft.primary_diagram is not None
    assert draft.primary_diagram.node_ids == {"api", "core"}
    assert draft.modules[0].module == "sample.core"


def test_draft_can_be_instantiated_with_defaults() -> None:
    draft = ArchitectureDraft(title="Legacy system")

    assert draft.diagrams == []
    assert draft.modules == []
    assert draft.primary_diagram is None


def test_edge_to_undeclared_node_is_rejected() -> None:
    payload = deepcopy(VALID_DRAFT)
    payload["diagrams"][0]["edges"][0]["target"] = "ghost"

    with pytest.raises(ValidationError, match="undeclared node ids"):
        _validate(payload)


def test_mermaid_node_id_with_space_is_rejected() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        MermaidNode(id="My Node", label="My Node")


def test_unknown_field_is_rejected() -> None:
    payload = deepcopy(VALID_DRAFT)
    payload["unexpected"] = "not in the schema"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        _validate(payload)


def test_unknown_nested_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MermaidNode(id="api", label="API", unexpected=True)
