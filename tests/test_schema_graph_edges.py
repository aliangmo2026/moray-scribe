"""Dangling-edge policy tests for SymbolGraph (R-1)."""

from __future__ import annotations

import pytest

from moray_scribe.exceptions import GraphError
from moray_scribe.schema import (
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)


def _node(symbol_id: str) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=SymbolKind.FUNCTION,
        module="graph",
        file="graph.py",
        lineno=1,
        end_lineno=2,
    )


def test_add_edge_allows_dangling_endpoints() -> None:
    alpha = _node("alpha")
    graph = SymbolGraph(nodes={alpha.id: alpha})

    graph.add_edge(SymbolEdge(source=alpha.id, target="missing", kind=EdgeKind.CALLS))
    graph.add_edge(SymbolEdge(source="missing", target=alpha.id, kind=EdgeKind.CALLS))

    assert graph.edge_count == 2


def test_validate_edges_reports_each_unknown_endpoint() -> None:
    alpha = _node("alpha")
    graph = SymbolGraph(
        nodes={alpha.id: alpha},
        edges=[
            SymbolEdge(source=alpha.id, target="missing", kind=EdgeKind.CALLS),
            SymbolEdge(source="ghost", target=alpha.id, kind=EdgeKind.IMPORTS),
        ],
    )

    problems = graph.validate_edges()

    assert len(problems) == 2
    assert any("target 'missing'" in problem for problem in problems)
    assert any("source 'ghost'" in problem for problem in problems)
    assert graph.validate_edges(known={alpha.id, "missing", "ghost"}) == []
    assert graph.validate_edges({alpha.id, "missing", "ghost"}) == []


def test_from_json_is_lax_for_incremental_graphs() -> None:
    alpha = _node("alpha")
    graph = SymbolGraph(
        nodes={alpha.id: alpha},
        edges=[SymbolEdge(source=alpha.id, target="missing", kind=EdgeKind.CALLS)],
    )

    restored = SymbolGraph.from_json(graph.to_json())

    assert restored.edge_count == 1
    assert restored.validate_edges() != []


def test_from_json_strict_rejects_dangling_edges() -> None:
    alpha = _node("alpha")
    graph = SymbolGraph(
        nodes={alpha.id: alpha},
        edges=[SymbolEdge(source=alpha.id, target="missing", kind=EdgeKind.CALLS)],
    )

    with pytest.raises(GraphError, match="dangling edges"):
        SymbolGraph.from_json_strict(graph.to_json())


def test_from_json_strict_accepts_closed_graph() -> None:
    alpha = _node("alpha")
    beta = _node("beta")
    graph = SymbolGraph(
        nodes={alpha.id: alpha, beta.id: beta},
        edges=[SymbolEdge(source=alpha.id, target=beta.id, kind=EdgeKind.CALLS)],
    )

    restored = SymbolGraph.from_json_strict(graph.to_json())

    assert restored.validate_edges() == []
    assert restored.edge_count == 1
