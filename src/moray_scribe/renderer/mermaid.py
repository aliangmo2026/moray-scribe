"""Render validated Mermaid diagram models as Mermaid source text.

All output is deterministic: nodes, edges and classDef blocks are emitted in a
stable sorted order so the same input always yields byte-identical text.
"""

from __future__ import annotations

from moray_scribe.exceptions import RenderError
from moray_scribe.schema import (
    ArchEdgeKind,
    DiagramKind,
    EdgeStyle,
    MermaidDiagram,
    MermaidEdge,
    MermaidNode,
    NodeKind,
)

_ARROW_BY_STYLE = {
    EdgeStyle.SOLID: "-->",
    EdgeStyle.DASHED: "-.->",
    EdgeStyle.DOTTED: "-.->",
    EdgeStyle.THICK: "==>",
}

_ROUNDED = frozenset({NodeKind.MODULE, NodeKind.PACKAGE, NodeKind.FUNCTION})

# Mermaid classDiagram relationship arrows, keyed by architecture edge kind.
_CLASS_REL = {
    ArchEdgeKind.INHERITS: "<|--",
    ArchEdgeKind.IMPLEMENTS: "<|..",
    ArchEdgeKind.COMPOSES: "*--",
    ArchEdgeKind.DEPENDS_ON: "-->",
    ArchEdgeKind.IMPORTS: "-->",
    ArchEdgeKind.CALLS: "-->",
    ArchEdgeKind.CONTAINS: "-->",
    ArchEdgeKind.USES: "-->",
    ArchEdgeKind.DATA_FLOW: "..>",
}

# Kinds whose arrowhead points at the *target* (UML generalization direction).
_GENERALIZATION = frozenset({ArchEdgeKind.INHERITS, ArchEdgeKind.IMPLEMENTS})


def _escape_label(text: str) -> str:
    """Escape a free-form string for use inside a quoted Mermaid label.

    Per-character (no regex): newlines become spaces, backslashes become
    forward slashes, and the five characters Mermaid treats specially inside
    labels (``"`` ``&`` ``<`` ``>`` ``|``) become ``#quot;`` ``#amp;``
    ``#lt;`` ``#gt;`` ``#124;``. Brackets and parentheses are left as-is
    because the label is wrapped in double quotes, where Mermaid tolerates them.

    .. warning::
       **Order trap (D-5.1).** This function flattens newlines to spaces and
       backslashes to forward slashes, so it will NOT produce a line break.
       For a Mermaid line break do NOT pass ``\\n`` or ``<br/>`` through here —
       append ``<br/>`` to the RETURN value instead::

           _escape_label("Auth\\n(entry)")              # wrong -> "Auth/(entry)"
           _escape_label("Auth") + "<br/>(entry)"       # right -> line break
    """
    out: list[str] = []
    for char in text:
        if char in ("\n", "\r"):
            out.append(" ")
        elif char == "\\":
            out.append("/")
        elif char == '"':
            out.append("#quot;")
        elif char == "&":
            out.append("#amp;")
        elif char == "<":
            out.append("#lt;")
        elif char == ">":
            out.append("#gt;")
        elif char == "|":
            out.append("#124;")
        else:
            out.append(char)
    return "".join(out)


def _sanitize_id(text: str, *, fallback_prefix: str = "Group") -> str:
    """Generate a Mermaid-safe subgraph id from arbitrary (possibly CJK) text.

    Rules: keep ``[A-Za-z0-9_]``, drop everything else (including Chinese);
    an empty result returns ``fallback_prefix``; a non-letter first character
    gets an ``L_`` prefix. The function is pure and testable — duplicate-id
    disambiguation (``_1`` suffixes) is done by the caller (``_render_flowchart``).
    """
    cleaned = "".join(
        char
        for char in text
        if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char == "_"
    )
    if not cleaned:
        return fallback_prefix
    if not (("a" <= cleaned[0] <= "z") or ("A" <= cleaned[0] <= "Z")):
        return f"L_{cleaned}"
    return cleaned


def _node_label(node: MermaidNode) -> str:
    """Escaped label plus any entry/hub annotation."""
    label = _escape_label(node.label)
    if node.kind is NodeKind.ENTRYPOINT:
        label += "<br/>(entry)"
    if node.critical:
        label += "<br/>(hub)"
    return label


def _node_shape(node: MermaidNode) -> str:
    """Render one flowchart node with the shape matching its kind."""
    label = _node_label(node)
    if node.kind is NodeKind.DATABASE:
        return f'{node.id}[("{label}")]'
    if node.kind is NodeKind.ENTRYPOINT:
        return f'{node.id}[/"{label}"/]'
    if node.kind in _ROUNDED:
        return f'{node.id}("{label}")'
    return f'{node.id}["{label}"]'


def _edge_arrow(edge: MermaidEdge) -> str:
    return _ARROW_BY_STYLE[edge.style]


def _render_flowchart(diagram: MermaidDiagram) -> str:
    lines = [f"flowchart {diagram.direction.value}"]

    grouped: dict[str, list[MermaidNode]] = {}
    ungrouped: list[MermaidNode] = []
    for node in diagram.nodes:
        if node.group:
            grouped.setdefault(node.group, []).append(node)
        else:
            ungrouped.append(node)

    # Disambiguate subgraph ids so distinct groups that sanitize to the same
    # base id (e.g. two Chinese layer names) stay separate subgraphs.
    group_id_map: dict[str, str] = {}
    used_ids: set[str] = set()

    def assign_id(group_name: str) -> str:
        if group_name in group_id_map:
            return group_id_map[group_name]
        base = _sanitize_id(group_name, fallback_prefix="Group")
        candidate = base
        counter = 1
        while candidate in used_ids:
            candidate = f"{base}_{counter}"
            counter += 1
        used_ids.add(candidate)
        group_id_map[group_name] = candidate
        return candidate

    for layer in sorted(grouped):
        subgraph_id = assign_id(layer)
        lines.append(f'    subgraph {subgraph_id}["{_escape_label(layer)}"]')
        for node in sorted(grouped[layer], key=lambda item: item.id):
            lines.append(f"        {_node_shape(node)}")
        lines.append("    end")

    for node in sorted(ungrouped, key=lambda item: item.id):
        lines.append(f"    {_node_shape(node)}")

    for edge in sorted(
        diagram.edges,
        key=lambda item: (item.source, item.target, item.kind.value, item.label),
    ):
        arrow = _edge_arrow(edge)
        if edge.label:
            lines.append(f'    {edge.source} {arrow}|"{_escape_label(edge.label)}"| {edge.target}')
        else:
            lines.append(f"    {edge.source} {arrow} {edge.target}")

    lines.extend(_class_defs(diagram))
    return "\n".join(lines)


def _class_defs(diagram: MermaidDiagram) -> list[str]:
    """Emit classDef + class assignment blocks for special node kinds."""
    blocks: list[str] = []
    external = sorted(node.id for node in diagram.nodes if node.kind is NodeKind.EXTERNAL)
    database = sorted(node.id for node in diagram.nodes if node.kind is NodeKind.DATABASE)
    entry = sorted(node.id for node in diagram.nodes if node.kind is NodeKind.ENTRYPOINT)

    if external:
        blocks.append("    classDef external fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 5")
        blocks.append(f"    class {','.join(external)} external")
    if database:
        blocks.append("    classDef database fill:#fff3e0,stroke:#f57c00")
        blocks.append(f"    class {','.join(database)} database")
    if entry:
        blocks.append("    classDef entry fill:#e1f5fe,stroke:#0288d1")
        blocks.append(f"    class {','.join(entry)} entry")
    return blocks


def _render_class_diagram(diagram: MermaidDiagram) -> str:
    lines = ["classDiagram"]
    external_ids = {node.id for node in diagram.nodes if node.kind is NodeKind.EXTERNAL}

    for node in sorted(diagram.nodes, key=lambda item: item.id):
        if node.id in external_ids:
            continue
        lines.append(f"    class {node.id} {{")
        lines.append(f"        <<{node.kind.value}>>")
        lines.append("    }")

    for edge in sorted(
        diagram.edges,
        key=lambda item: (item.source, item.target, item.kind.value, item.label),
    ):
        if edge.source in external_ids or edge.target in external_ids:
            continue
        arrow = _CLASS_REL.get(edge.kind, "-->")
        if edge.kind in _GENERALIZATION:
            line = f"    {edge.target} {arrow} {edge.source}"
        else:
            line = f"    {edge.source} {arrow} {edge.target}"
        if edge.label:
            line += f" : {_escape_label(edge.label)}"
        lines.append(line)
    return "\n".join(lines)


def diagram_to_mermaid(diagram: MermaidDiagram) -> str:
    """Render one diagram to Mermaid syntax, starting at its declaration line."""
    if diagram.kind is DiagramKind.FLOWCHART:
        return _render_flowchart(diagram)
    if diagram.kind is DiagramKind.CLASS_DIAGRAM:
        return _render_class_diagram(diagram)
    raise RenderError(f"unsupported diagram kind: {diagram.kind}")


def diagram_to_mermaid_fence(diagram: MermaidDiagram, *, lang: str = "mermaid") -> str:
    """Wrap :func:`diagram_to_mermaid` output in a fenced code block."""
    return f"```{lang}\n{diagram_to_mermaid(diagram)}\n```"
