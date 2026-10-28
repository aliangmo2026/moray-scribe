"""Budget-aware graph pruning before the graph is sent to a language model."""

from __future__ import annotations

from collections import defaultdict

from loguru import logger

from moray_scribe.schema import SymbolEdge, SymbolGraph, SymbolNode

from .analyzer import compute_pagerank


def _adjacency(graph: SymbolGraph) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build outgoing and incoming adjacency maps over existing nodes only."""
    out: dict[str, set[str]] = defaultdict(set)
    inc: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.source in graph.nodes and edge.target in graph.nodes:
            out[edge.source].add(edge.target)
            inc[edge.target].add(edge.source)
    return out, inc


def prune(
    graph: SymbolGraph,
    *,
    max_nodes: int = 200,
    max_edges: int = 500,
    hub_ratio: float = 0.2,
    hop: int = 2,
    drop_dead_candidates: bool = True,
) -> SymbolGraph:
    """Return a budget-trimmed copy of ``graph``; the input graph is never mutated.

    Hubs (top-PageRank) and entry points seed a bidirectional BFS of ``hop``
    rounds; remaining budget is filled by PageRank. Edges whose endpoints fall
    outside the keep set are dropped, then edges are capped by the product of
    endpoint PageRank.
    """
    work = graph.model_copy(deep=True)
    compute_pagerank(work)
    nodes: dict[str, SymbolNode] = work.nodes

    if len(nodes) <= max_nodes and len(work.edges) <= max_edges:
        return work

    ranked = sorted(nodes.values(), key=lambda n: n.pagerank, reverse=True)
    hub_count = max(10, int(max_nodes * hub_ratio))
    keep: set[str] = {n.id for n in ranked[:hub_count]}
    keep |= {n.id for n in nodes.values() if n.is_entry}

    out_adj, in_adj = _adjacency(work)
    frontier = set(keep)
    for _ in range(max(0, hop)):
        neighbors: set[str] = set()
        for node_id in frontier:
            neighbors |= out_adj.get(node_id, set())
            neighbors |= in_adj.get(node_id, set())
        neighbors -= keep
        if not neighbors:
            break
        ordered = sorted(
            (nodes[n] for n in neighbors if n in nodes),
            key=lambda n: n.pagerank,
            reverse=True,
        )
        added: list[str] = []
        for node in ordered:
            if len(keep) >= max_nodes:
                break
            keep.add(node.id)
            added.append(node.id)
        frontier = set(added)

    for node in ranked:
        if len(keep) >= max_nodes:
            break
        if drop_dead_candidates and node.is_dead_candidate:
            continue
        keep.add(node.id)

    if len(keep) > max_nodes:
        logger.warning("prune: keep set exceeds budget, truncating to {}", max_nodes)
        keep = {n.id for n in ranked if n.id in keep}
        keep = set(sorted(keep, key=lambda nid: nodes[nid].pagerank, reverse=True)[:max_nodes])

    new_nodes = {
        node_id: nodes[node_id].model_copy(deep=True) for node_id in keep if node_id in nodes
    }
    new_edges = [
        edge.model_copy(deep=True)
        for edge in work.edges
        if edge.source in keep and edge.target in keep
    ]

    if len(new_edges) > max_edges:

        def _edge_score(edge: SymbolEdge) -> float:
            return nodes[edge.source].pagerank * nodes[edge.target].pagerank

        new_edges.sort(key=_edge_score, reverse=True)
        new_edges = new_edges[:max_edges]

    root = work.root if work.root in new_nodes else None
    return SymbolGraph(nodes=new_nodes, edges=new_edges, root=root, metadata=dict(work.metadata))


def prune_for_prompt(
    graph: SymbolGraph,
    *,
    max_nodes: int = 200,
    max_edges: int = 500,
) -> SymbolGraph:
    """Describer-facing entry point: prune while dropping dead-code candidates."""
    return prune(graph, max_nodes=max_nodes, max_edges=max_edges, drop_dead_candidates=True)
