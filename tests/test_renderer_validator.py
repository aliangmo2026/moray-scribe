"""Unit tests for the advisory Mermaid diagram validator."""

from __future__ import annotations

from moray_scribe.renderer import validate_diagram
from moray_scribe.schema import (
    ArchEdgeKind,
    DiagramKind,
    MermaidDiagram,
    MermaidEdge,
    MermaidNode,
    NodeKind,
)


def _n(
    node_id: str,
    label: str = "Label",
    kind: NodeKind = NodeKind.MODULE,
    group: str | None = None,
) -> MermaidNode:
    return MermaidNode(id=node_id, label=label, kind=kind, group=group)


def _diagram(nodes, edges=None) -> MermaidDiagram:
    return MermaidDiagram(
        kind=DiagramKind.FLOWCHART,
        nodes=nodes,
        edges=edges or [],
    )


def test_validator_too_few_nodes() -> None:
    warnings = validate_diagram(_diagram([_n("A"), _n("B")]))

    assert any("too coarse" in w for w in warnings)


def test_validator_no_edges() -> None:
    warnings = validate_diagram(
        _diagram([_n("A"), _n("B"), _n("C")], [])
    )

    assert any("no edges" in w for w in warnings)


def test_validator_duplicate_layers() -> None:
    # "_abc" and "L__abc" are both valid group ids but normalize to "L__abc".
    warnings = validate_diagram(
        _diagram([_n("A", group="_abc"), _n("B", group="L__abc")])
    )

    assert any("disambiguate" in w for w in warnings)


def test_validator_clean() -> None:
    edges = [MermaidEdge(source="A", target="B", kind=ArchEdgeKind.CALLS)]
    warnings = validate_diagram(
        _diagram([_n("A", group="Alpha"), _n("B", group="Beta"), _n("C")], edges)
    )

    assert warnings == []


def test_validator_code_fence_label() -> None:
    warnings = validate_diagram(_diagram([_n("A", "has ``` fence"), _n("B"), _n("C")]))

    assert any("code fence" in w for w in warnings)
