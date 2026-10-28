"""Symbol graph CRUD and serialization tests."""

from __future__ import annotations

import pytest

from moray_scribe.exceptions import SchemaError
from moray_scribe.schema import (
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)


def _node(symbol_id: str) -> SymbolNode:
    short_name = symbol_id.rsplit(".", 1)[-1]
    return SymbolNode(
        id=symbol_id,
        name=short_name,
        qualname=short_name,
        kind=SymbolKind.FUNCTION,
        module="sample.core",
        file="core.py",
        lineno=1,
        end_lineno=4,
    )


def test_graph_add_query_and_remove() -> None:
    alpha = _node("sample.core.alpha")
    beta = _node("sample.core.beta")
    graph = SymbolGraph()

    graph.add_node(alpha)
    graph.add_node(beta)
    assert graph.node_count == 2
    assert graph.get_node(alpha.id) is alpha
    assert graph.get_node("sample.core.missing") is None

    graph.add_edge(SymbolEdge(source=alpha.id, target=beta.id, kind=EdgeKind.CALLS))
    assert graph.edge_count == 1
    assert graph.has_edge(alpha.id, beta.id, kind=EdgeKind.CALLS)
    assert not graph.has_edge(beta.id, alpha.id)

    assert graph.remove_node(alpha.id) is True
    assert graph.node_count == 1
    assert graph.edge_count == 0
    assert graph.remove_node(alpha.id) is False


def test_graph_rejects_duplicate_nodes() -> None:
    alpha = _node("sample.core.alpha")
    graph = SymbolGraph(nodes={alpha.id: alpha})

    with pytest.raises(SchemaError):
        graph.add_node(alpha)


def test_graph_json_round_trip() -> None:
    alpha = _node("sample.core.alpha")
    beta = _node("sample.core.beta")
    graph = SymbolGraph(
        nodes={alpha.id: alpha, beta.id: beta},
        edges=[
            SymbolEdge(
                source=alpha.id,
                target=beta.id,
                kind=EdgeKind.CALLS,
                weight=2.0,
                linenos=[3, 9],
            )
        ],
        root=alpha.id,
    )

    restored = SymbolGraph.from_json(graph.to_json())

    assert restored == graph
    assert restored.root == alpha.id
    assert restored.edges[0].kind is EdgeKind.CALLS
    assert restored.get_node(beta.id) is not None
