"""Unit tests for describer prompt constants and constraint reflection."""

from __future__ import annotations

from moray_scribe.describer import SYSTEM_PROMPT, USER_TEMPLATE, build_constraint_hints
from moray_scribe.schema import ArchitectureDraft


def test_build_constraint_hints_mermaid_id() -> None:
    hints = build_constraint_hints(ArchitectureDraft)

    assert any("A-Za-z" in hint for hint in hints)


def test_build_constraint_hints_edge_refs() -> None:
    hints = build_constraint_hints(ArchitectureDraft)

    assert any("MermaidEdge" in hint and "MermaidNode.id" in hint for hint in hints)


def test_build_constraint_hints_minimum_count() -> None:
    hints = build_constraint_hints(ArchitectureDraft)

    assert len(hints) >= 3


def test_system_prompt_contains_critical_rules() -> None:
    assert "ONLY valid JSON" in SYSTEM_PROMPT
    assert "NEVER invent" in SYSTEM_PROMPT
    assert "architecture" in SYSTEM_PROMPT.lower()


def test_user_template_placeholders() -> None:
    assert "{nodes_json}" in USER_TEMPLATE
    assert "{edges_json}" in USER_TEMPLATE
    assert "{known_gaps}" in USER_TEMPLATE
    assert "{project_name}" in USER_TEMPLATE


def test_system_prompt_lists_mermaid_node_fields() -> None:
    assert "MermaidNode" in SYSTEM_PROMPT
    for field in ("id", "label", "kind", "group", "symbol_ids"):
        assert f'"{field}"' in SYSTEM_PROMPT
    # dependencies is explicitly forbidden inside nodes.
    assert "dependencies" in SYSTEM_PROMPT
    assert "NEVER inside a node" in SYSTEM_PROMPT or "not inside nodes" in SYSTEM_PROMPT


def test_system_prompt_lists_mermaid_edge_fields() -> None:
    assert "MermaidEdge" in SYSTEM_PROMPT
    for field in ("source", "target", "kind", "style"):
        assert f'"{field}"' in SYSTEM_PROMPT


def test_system_prompt_contains_r1_r2_rules() -> None:
    assert "R1." in SYSTEM_PROMPT
    assert "flowchart" in SYSTEM_PROMPT
    assert "R2." in SYSTEM_PROMPT
    assert "60%" in SYSTEM_PROMPT
