"""Unit tests for Mermaid rendering, escaping and shapes."""

from __future__ import annotations

import hashlib

import pytest

from moray_scribe.renderer import diagram_to_mermaid, diagram_to_mermaid_fence
from moray_scribe.schema import (
    ArchEdgeKind,
    DiagramKind,
    Direction,
    EdgeStyle,
    MermaidDiagram,
    MermaidEdge,
    MermaidNode,
    NodeKind,
)


def _n(
    node_id: str,
    label: str,
    kind: NodeKind = NodeKind.MODULE,
    group: str | None = None,
) -> MermaidNode:
    return MermaidNode(id=node_id, label=label, kind=kind, group=group)


def _e(
    source: str,
    target: str,
    style: EdgeStyle = EdgeStyle.SOLID,
    label: str = "",
) -> MermaidEdge:
    return MermaidEdge(source=source, target=target, style=style, label=label)


def test_flowchart_basic() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[_e("A", "B")],
        )
    )

    assert text.startswith("flowchart TD")
    assert 'A("Alpha")' in text
    assert "A --> B" in text


def test_flowchart_direction_respected() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            direction=Direction.LR,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[_e("A", "B")],
        )
    )

    assert text.startswith("flowchart LR")


def test_escape_label_quotes() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("A", 'He said "hi"')])
    )

    assert "#quot;" in text
    assert '"hi"' not in text
    assert "#quot;hi#quot;" in text


def test_escape_label_square_brackets() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("A", "arr[0]")])
    )

    # Brackets are preserved inside the quoted label.
    assert '"arr[0]"' in text


def test_escape_label_newlines() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("A", "line1\nline2")])
    )

    assert "line1 line2" in text


def test_node_shape_by_kind_database() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("DB", "Store", NodeKind.DATABASE)])
    )

    assert 'DB[("Store")]' in text


def test_node_shape_by_kind_entrypoint() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("Main", "Main", NodeKind.ENTRYPOINT)])
    )

    # Parallelogram shape markers plus the (entry) annotation.
    assert "Main[/" in text
    assert "/]" in text
    assert "(entry)" in text


def test_external_classdef_appended() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("Ext", "External", NodeKind.EXTERNAL)],
        )
    )

    assert "classDef external" in text
    assert "class Ext external" in text


def test_layer_subgraph_grouping() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[
                _n("A", "Alpha", group="Presentation"),
                _n("B", "Beta", group="Domain"),
                _n("C", "Gamma"),
            ],
        )
    )

    assert text.count("subgraph ") == 2
    assert 'subgraph Presentation["Presentation"]' in text
    assert 'subgraph Domain["Domain"]' in text
    assert 'C("Gamma")' in text


def test_edge_style_dashed() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[_e("A", "B", EdgeStyle.DASHED)],
        )
    )

    assert "A -.-> B" in text


def test_edge_style_thick() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[_e("A", "B", EdgeStyle.THICK)],
        )
    )

    assert "A ==> B" in text


def test_edge_label_escaped() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[MermaidEdge(source="A", target="B", label='via "x"')],
        )
    )

    assert 'A -->|"via #quot;x#quot;"| B' in text


def test_class_diagram_renders() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.CLASS_DIAGRAM,
            nodes=[_n("Domain", "Domain", NodeKind.MODULE), _n("Repo", "Repo", NodeKind.CLASS)],
            edges=[MermaidEdge(source="Repo", target="Domain", kind=ArchEdgeKind.INHERITS)],
        )
    )

    assert text.startswith("classDiagram")
    assert "class Repo {" in text
    assert "<|--" in text
    assert "Domain <|-- Repo" in text


def test_class_diagram_skips_external() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.CLASS_DIAGRAM,
            nodes=[
                _n("A", "A", NodeKind.CLASS),
                _n("Ext", "External", NodeKind.EXTERNAL),
            ],
        )
    )

    assert "class Ext" not in text
    assert "class A {" in text


def test_mermaid_determinism(sample_draft) -> None:
    diagram = sample_draft.diagrams[0]

    first = diagram_to_mermaid(diagram)
    second = diagram_to_mermaid(diagram)

    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()


def test_mermaid_no_trailing_newline() -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("A", "Alpha")])
    )

    assert not text.endswith("\n")


def test_diagram_to_mermaid_fence() -> None:
    fence = diagram_to_mermaid_fence(
        MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[_n("A", "Alpha")])
    )

    assert fence.startswith("```mermaid\n")
    assert fence.endswith("\n```")


@pytest.mark.parametrize("style", list(EdgeStyle))
def test_all_edge_styles_have_arrows(style: EdgeStyle) -> None:
    text = diagram_to_mermaid(
        MermaidDiagram(
            kind=DiagramKind.FLOWCHART,
            nodes=[_n("A", "Alpha"), _n("B", "Beta")],
            edges=[_e("A", "B", style)],
        )
    )

    assert "A " in text and " B" in text
