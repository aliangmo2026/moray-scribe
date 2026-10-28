"""D-5.1: escaping must happen BEFORE a <br/> line break is appended."""

from __future__ import annotations

from moray_scribe.renderer import diagram_to_mermaid
from moray_scribe.renderer.mermaid import _escape_label
from moray_scribe.schema import DiagramKind, MermaidDiagram, MermaidNode, NodeKind


def test_escape_label_then_br_renders_newline() -> None:
    # D-5.1 principle: composing AFTER escape keeps the break intact.
    composed = _escape_label("Main") + "<br/>(entry)"
    assert composed == "Main<br/>(entry)"

    # The renderer appends the break after escaping for ENTRYPOINT nodes, so
    # the raw <br/> reaches the output (it is not escaped to #lt;br/#gt;).
    node = MermaidNode(id="Main", label="Main", kind=NodeKind.ENTRYPOINT)
    text = diagram_to_mermaid(MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[node]))

    assert "<br/>(entry)" in text
    assert "#lt;br/#gt;" not in text


def test_label_br_stuffed_in_label_gets_escaped() -> None:
    # Counter-example: putting <br/> in the label value itself gets escaped,
    # which is why the break MUST be appended after _escape_label, not inside it.
    node = MermaidNode(id="X", label="line<br/>break")
    text = diagram_to_mermaid(MermaidDiagram(kind=DiagramKind.FLOWCHART, nodes=[node]))

    assert "#lt;br/#gt;" in text
    assert "<br/>" not in text


def test_escape_label_newline_is_flattened_not_a_break() -> None:
    # Regression: passing a literal \n (backslash-n) through escape must NOT
    # yield a line break — the backslash becomes a forward slash.
    escaped = _escape_label("Main\n(entry)")
    assert escaped == "Main (entry)"  # real newline -> space

    escaped_literal = _escape_label("Main\\n(entry)")
    assert escaped_literal == "Main/n(entry)"  # backslash -> slash, not a break
    assert "\n" not in escaped_literal
    assert "<br/>" not in escaped_literal
