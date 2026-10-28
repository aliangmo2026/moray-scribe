"""D-5.3: Chinese group names are accepted by the schema and sanitized at render."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moray_scribe.renderer import diagram_to_mermaid
from moray_scribe.schema import DiagramKind, MermaidDiagram, MermaidNode, NodeKind


def _node(node_id: str, group: str | None = None) -> MermaidNode:
    return MermaidNode(id=node_id, label=node_id, kind=NodeKind.MODULE, group=group)


def test_group_chinese_accepted_by_schema() -> None:
    node = _node("A", group="表示层")

    assert node.group == "表示层"


def test_group_control_chars_rejected() -> None:
    with pytest.raises(ValidationError):
        _node("A", group="bad\x00group")


def test_group_chinese_subgraph_id_sanitized() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_node("A", group="表示层")])
    )

    # ASCII id uses the fallback prefix; the display label keeps the Chinese name.
    assert 'subgraph Group["表示层"]' in text


def test_group_collision_disambiguated() -> None:
    # Two Chinese groups both sanitize to the fallback "Group" -> one gets _1.
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_node("A", group="表示层"), _node("B", group="数据层")],
        )
    )

    assert text.count("subgraph ") == 2
    # Both Chinese display names are preserved.
    assert '["表示层"]' in text
    assert '["数据层"]' in text
    # Distinct subgraph ids (base + _1) — order depends on Unicode sort.
    assert "subgraph Group[" in text
    assert "subgraph Group_1[" in text
