"""Compact prompt-facing serialization of a symbol graph for the describer."""

from __future__ import annotations

from moray_scribe.schema import SymbolEdge, SymbolGraph, SymbolNode


def _is_hub(nodes: list[SymbolNode]) -> dict[str, bool]:
    """Mark the top-20% PageRank nodes above a 0.001 floor as hubs."""
    if not nodes:
        return {}
    scores = sorted((node.pagerank for node in nodes), reverse=True)
    cutoff_index = min(int(len(nodes) * 0.2), len(nodes) - 1)
    threshold = scores[cutoff_index]
    return {
        node.id: node.pagerank >= threshold and node.pagerank > 0.001 for node in nodes
    }


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit]


def to_prompt_view(
    graph: SymbolGraph,
    *,
    include_dead: bool = False,
    max_description_chars: int = 80,
) -> dict[str, list[dict[str, object]]]:
    """Render the graph as the abbreviated payload the describer prompt embeds.

    Keys are deliberately short to keep tokens down: nodes use ``i`` (id),
    ``k`` (kind), ``n`` (name), ``m`` (module), ``h`` (hub), ``e`` (entry),
    ``d`` (docstring), plus ``r`` (HTTP route, when detected) and ``o=1``
    (ORM model tag, when detected); edges use ``s`` (source), ``d`` (target),
    ``k`` (kind), ``w`` (weight), plus ``inf`` (present and 1) only on
    LLM-inferred edges. Dead-code candidates are dropped unless
    ``include_dead``.
    """
    nodes = [
        node
        for node in graph.nodes.values()
        if include_dead or not node.is_dead_candidate
    ]
    hubs = _is_hub(nodes)

    def _view_node(node: SymbolNode) -> dict[str, object]:
        view: dict[str, object] = {
            "i": node.id,
            "k": node.kind.value,
            "n": node.name,
            "m": node.module,
            "h": hubs.get(node.id, False),
            "e": node.is_entry,
            "d": _truncate(node.docstring, max_description_chars),
        }
        if node.route is not None:
            methods = "/".join(node.route.methods) or "GET"
            view["r"] = f"{methods} {node.route.path}"
        if "orm_model" in node.tags:
            view["o"] = 1
        return view

    view_nodes = [_view_node(node) for node in nodes]

    keep_ids = {node.id for node in nodes}

    def _view_edge(edge: SymbolEdge) -> dict[str, object]:
        view: dict[str, object] = {
            "s": edge.source,
            "d": edge.target,
            "k": edge.kind.value,
            "w": edge.weight,
        }
        if edge.inferred:
            view["inf"] = 1
        return view

    view_edges = [
        _view_edge(edge)
        for edge in graph.edges
        if edge.source in keep_ids and edge.target in keep_ids
    ]
    return {"n": view_nodes, "e": view_edges}
