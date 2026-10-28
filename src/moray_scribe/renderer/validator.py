"""Pre-render checks that flag likely Mermaid problems without blocking output."""

from __future__ import annotations

from moray_scribe.schema import MermaidDiagram

from .mermaid import _sanitize_id


def validate_diagram(diagram: MermaidDiagram) -> list[str]:
    """Return human-readable warnings; an empty list means no concerns found.

    Checks are advisory only: schema validation already guarantees referential
    integrity, so these flag readability and rendering risks.
    """
    warnings: list[str] = []

    ids = [node.id for node in diagram.nodes]
    known = set(ids)

    for node in diagram.nodes:
        if "```" in node.label:
            warnings.append(
                f"node {node.id} label contains a code fence ``` which Mermaid cannot escape"
            )

    seen: set[str] = set()
    duplicates: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            duplicates.add(node_id)
        seen.add(node_id)
    for node_id in sorted(duplicates):
        warnings.append(f"duplicate node id {node_id}")

    for edge in diagram.edges:
        if edge.source not in known:
            warnings.append(f"edge source {edge.source} is not a declared node")
        if edge.target not in known:
            warnings.append(f"edge target {edge.target} is not a declared node")

    # Distinct layer names that normalize to the same base subgraph id.
    group_to_sanitized: dict[str, str] = {}
    for node in diagram.nodes:
        if node.group:
            group_to_sanitized.setdefault(node.group, _sanitize_id(node.group))
    sanitized_to_group: dict[str, str] = {}
    for group, sanitized in sorted(group_to_sanitized.items()):
        prior = sanitized_to_group.get(sanitized)
        if prior is not None and prior != group:
            warnings.append(
                f"layers {prior!r} and {group!r} normalize to the same base subgraph "
                f"id {sanitized!r}; the renderer will disambiguate them"
            )
        else:
            sanitized_to_group[sanitized] = group

    if len(diagram.nodes) < 3:
        warnings.append(
            f"only {len(diagram.nodes)} node(s); this may be too coarse an abstraction"
        )
    if not diagram.edges:
        warnings.append("diagram has no edges; it may show nodes without relationships")

    return warnings
