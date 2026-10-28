"""Unit tests for budget-aware graph pruning."""

from __future__ import annotations

from moray_scribe.grapher import compute_pagerank, prune, prune_for_prompt
from moray_scribe.schema import (
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)


def _node(
    symbol_id: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    is_entry: bool = False,
    is_dead_candidate: bool = False,
) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module="m",
        file="m.py",
        lineno=1,
        end_lineno=2,
        is_entry=is_entry,
        is_dead_candidate=is_dead_candidate,
    )


def _star_graph(leaf_count: int) -> tuple[SymbolGraph, dict[str, SymbolNode]]:
    """Build a hub pointed to by many leaves so the hub ranks highest."""
    hub = _node("hub")
    leaves = {f"leaf{i}": _node(f"leaf{i}") for i in range(leaf_count)}
    edges = [
        SymbolEdge(source=leaf_id, target="hub", kind=EdgeKind.CALLS)
        for leaf_id in leaves
    ]
    nodes = {"hub": hub, **leaves}
    return SymbolGraph(nodes=nodes, edges=edges), nodes


def test_no_prune_when_small() -> None:
    graph, _ = _star_graph(3)

    result = prune(graph, max_nodes=200, max_edges=500)

    assert result.node_count == graph.node_count
    assert result.edge_count == graph.edge_count


def test_prune_keeps_hubs() -> None:
    graph, _ = _star_graph(100)
    compute_pagerank(graph)
    hub_rank = graph.nodes["hub"].pagerank

    result = prune(graph, max_nodes=20, max_edges=100)

    assert result.node_count <= 20
    assert "hub" in result.nodes
    assert graph.nodes["hub"].pagerank == hub_rank  # original untouched


def test_prune_keeps_entry_points() -> None:
    hub = _node("hub")
    entry = _node("entry", is_entry=True)
    leaves = {f"leaf{i}": _node(f"leaf{i}") for i in range(60)}
    nodes = {"hub": hub, "entry": entry, **leaves}
    edges = [
        SymbolEdge(source=leaf_id, target="hub", kind=EdgeKind.CALLS)
        for leaf_id in leaves
    ]
    graph = SymbolGraph(nodes=nodes, edges=edges)

    result = prune(graph, max_nodes=10, max_edges=50)

    assert "entry" in result.nodes
    assert "hub" in result.nodes


def test_prune_does_not_mutate_original() -> None:
    graph, _ = _star_graph(80)
    before = graph.model_copy(deep=True)

    prune(graph, max_nodes=15, max_edges=40)

    assert graph.node_count == before.node_count
    assert graph.edge_count == before.edge_count
    assert graph.nodes["hub"].pagerank == before.nodes["hub"].pagerank


def test_prune_edges_both_ends_present() -> None:
    graph, _ = _star_graph(90)

    result = prune(graph, max_nodes=12, max_edges=30)

    assert result.validate_edges() == []


def test_prune_drops_dead_candidates() -> None:
    hub = _node("hub")
    live = {f"live{i}": _node(f"live{i}") for i in range(30)}
    dead = {f"dead{i}": _node(f"dead{i}", is_dead_candidate=True) for i in range(5)}
    nodes = {"hub": hub, **live, **dead}
    edges = [
        SymbolEdge(source=leaf_id, target="hub", kind=EdgeKind.CALLS)
        for leaf_id in live
    ]
    graph = SymbolGraph(nodes=nodes, edges=edges)

    result = prune(graph, max_nodes=10, max_edges=50, drop_dead_candidates=True)

    kept_ids = set(result.nodes)
    assert not any(node_id.startswith("dead") for node_id in kept_ids)
    assert "hub" in kept_ids


def test_prune_for_prompt_matches_default() -> None:
    graph, _ = _star_graph(60)

    result = prune_for_prompt(graph, max_nodes=10, max_edges=30)

    assert result.node_count <= 10
    assert result.validate_edges() == []
