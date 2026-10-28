"""Centrality, degree and dead-code analysis over a symbol graph."""

from __future__ import annotations

import networkx as nx

from moray_scribe.schema import EdgeKind, SymbolGraph, SymbolKind

# Only definitions can be dead code; modules and imports are structural.
_DEAD_KINDS = frozenset(
    {
        SymbolKind.FUNCTION,
        SymbolKind.METHOD,
        SymbolKind.CLASS,
    }
)

# Names that mark a definition as an entry point regardless of references.
_ENTRY_NAMES = frozenset({"main", "__main__", "__init__"})

# Decorator prefixes that register a function as an entry point (routes, CLIs).
_ENTRY_DECORATOR_PREFIXES = (
    "app.route",
    "app.get",
    "app.post",
    "app.put",
    "app.patch",
    "app.delete",
    "click.command",
    "typer.",
)


def _is_entry_decorator(text: str) -> bool:
    """Whether a decorator source snippet registers an entry point."""
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _ENTRY_DECORATOR_PREFIXES)


def annotate_degrees(graph: SymbolGraph) -> None:
    """Fill ``in_degree`` and ``out_degree`` on every node in place.

    Counts are per-edge (duplicates were already merged by the builder), not
    weighted sums.
    """
    in_counts = {node_id: 0 for node_id in graph.nodes}
    out_counts = {node_id: 0 for node_id in graph.nodes}
    for edge in graph.edges:
        if edge.source in out_counts:
            out_counts[edge.source] += 1
        if edge.target in in_counts:
            in_counts[edge.target] += 1
    for node_id, node in graph.nodes.items():
        node.in_degree = in_counts[node_id]
        node.out_degree = out_counts[node_id]


def _external_in_degree(graph: SymbolGraph) -> dict[str, int]:
    """Incoming edges excluding CONTAINS, i.e. references from outside a container.

    CONTAINS edges are structural (a module always contains its functions), so
    counting them would make every symbol look referenced. Dead-code detection
    needs the external count instead.
    """
    counts = {node_id: 0 for node_id in graph.nodes}
    for edge in graph.edges:
        if edge.kind is EdgeKind.CONTAINS:
            continue
        if edge.target in counts:
            counts[edge.target] += 1
    return counts


def compute_pagerank(
    graph: SymbolGraph,
    *,
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute PageRank with networkx and write scores back onto the nodes.

    Dangling edges (endpoints outside ``graph.nodes``) are filtered out first.
    Graphs with fewer than two nodes get a score of 1.0 everywhere.
    """
    valid = set(graph.nodes)
    directed = nx.DiGraph()
    directed.add_nodes_from(valid)
    for edge in graph.edges:
        if edge.source in valid and edge.target in valid:
            directed.add_edge(edge.source, edge.target, weight=edge.weight)

    if len(valid) < 2:
        scores = {node_id: 1.0 for node_id in valid}
    else:
        raw = nx.pagerank(
            directed,
            alpha=alpha,
            max_iter=max_iter,
            tol=tol,
            weight="weight",
        )
        scores = {str(node_id): float(value) for node_id, value in raw.items()}

    for node_id, score in scores.items():
        node = graph.nodes.get(node_id)
        if node is not None:
            node.pagerank = score
    return scores


def detect_entry_points(graph: SymbolGraph) -> list[str]:
    """Return ids of definitions that act as program entry points.

    A node qualifies when its module is ``__main__``, its name is
    ``main``/``__main__``/``__init__``, or a decorator registers a route or CLI
    command.
    """
    entries: list[str] = []
    for node_id, node in graph.nodes.items():
        if node.module == "__main__":
            entries.append(node_id)
            continue
        if node.name in _ENTRY_NAMES:
            entries.append(node_id)
            continue
        if any(_is_entry_decorator(text) for text in node.decorators):
            entries.append(node_id)
    return entries


def detect_dead_code(graph: SymbolGraph) -> list[str]:
    """Return candidate dead-code node ids without mutating the graph.

    A node qualifies when it is a class/function/method with no *external*
    incoming edge, is not an entry point, has a public name and is not a
    package facade. CONTAINS edges are ignored because they are structural.
    """
    entry_ids = set(detect_entry_points(graph))
    external_in = _external_in_degree(graph)
    dead: list[str] = []
    for node_id, node in graph.nodes.items():
        if node.kind not in _DEAD_KINDS:
            continue
        if external_in.get(node_id, 0) != 0:
            continue
        if node_id in entry_ids:
            continue
        if node.name.startswith("_"):
            continue
        if node.is_module_facade:
            continue
        dead.append(node_id)
    return dead
