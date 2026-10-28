"""Unit tests for graph analysis: degrees, PageRank, dead code, entry points."""

from __future__ import annotations

import pytest

from moray_scribe.grapher import (
    annotate_degrees,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
)
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
    name: str | None = None,
    decorators: list[str] | None = None,
    module: str = "m",
) -> SymbolNode:
    short = name or symbol_id.rsplit(".", 1)[-1]
    return SymbolNode(
        id=symbol_id,
        name=short,
        qualname=symbol_id,
        kind=kind,
        module=module,
        file=f"{module}.py",
        lineno=1,
        end_lineno=2,
        decorators=decorators or [],
    )


def _edge(source: str, target: str, kind: EdgeKind = EdgeKind.CALLS) -> SymbolEdge:
    return SymbolEdge(source=source, target=target, kind=kind)


def test_degrees_basic() -> None:
    a, b, c = _node("a"), _node("b"), _node("c")
    graph = SymbolGraph(
        nodes={"a": a, "b": b, "c": c},
        edges=[_edge("a", "b"), _edge("a", "c"), _edge("b", "c")],
    )

    annotate_degrees(graph)

    assert (a.in_degree, a.out_degree) == (0, 2)
    assert (b.in_degree, b.out_degree) == (1, 1)
    assert (c.in_degree, c.out_degree) == (2, 0)


def test_pagerank_sums_to_one() -> None:
    a, b, c = _node("a"), _node("b"), _node("c")
    graph = SymbolGraph(
        nodes={"a": a, "b": b, "c": c},
        edges=[_edge("b", "a"), _edge("c", "a")],
    )

    scores = compute_pagerank(graph)

    assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)


def test_pagerank_hub_ranks_higher() -> None:
    hub = _node("hub")
    leaves = {f"leaf{i}": _node(f"leaf{i}") for i in range(5)}
    graph = SymbolGraph(
        nodes={"hub": hub, **leaves},
        edges=[_edge(leaf_id, "hub") for leaf_id in leaves],
    )

    compute_pagerank(graph)

    assert hub.pagerank > max(node.pagerank for node in leaves.values())
    assert graph.nodes["hub"].pagerank == hub.pagerank


def test_pagerank_single_node() -> None:
    only = _node("only")
    graph = SymbolGraph(nodes={"only": only})

    scores = compute_pagerank(graph)

    assert scores == {"only": 1.0}
    assert graph.nodes["only"].pagerank == 1.0


def test_pagerank_filters_dangling_edges() -> None:
    a = _node("a")
    graph = SymbolGraph(
        nodes={"a": a},
        edges=[_edge("a", "ghost")],
    )

    scores = compute_pagerank(graph)

    assert set(scores) == {"a"}


def test_dead_code_detects_isolated() -> None:
    orphan = _node("orphan")
    referenced = _node("referenced")
    caller = _node("caller")
    graph = SymbolGraph(
        nodes={"orphan": orphan, "referenced": referenced, "caller": caller},
        edges=[_edge("caller", "referenced")],
    )

    dead = detect_dead_code(graph)

    # `orphan` and `caller` are both unreferenced; `referenced` has an incoming edge.
    assert "orphan" in dead
    assert "caller" in dead
    assert "referenced" not in dead


def test_dead_code_skips_private() -> None:
    helper = _node("_helper")
    graph = SymbolGraph(nodes={"_helper": helper})

    assert detect_dead_code(graph) == []


def test_dead_code_skips_entry() -> None:
    main = _node("main")
    graph = SymbolGraph(nodes={"main": main})

    assert detect_dead_code(graph) == []
    assert detect_entry_points(graph) == ["main"]


def test_dead_code_skips_decorated_route() -> None:
    index = _node("index", decorators=['app.route("/")'])
    graph = SymbolGraph(nodes={"index": index})

    assert detect_dead_code(graph) == []
    assert detect_entry_points(graph) == ["index"]


def test_dead_code_ignores_contains_edges() -> None:
    module = _node("m", kind=SymbolKind.MODULE, name="m")
    func = _node("m.func")
    graph = SymbolGraph(
        nodes={"m": module, "m.func": func},
        edges=[_edge("m", "m.func", EdgeKind.CONTAINS)],
    )

    # The structural CONTAINS edge must not mask dead-code candidacy.
    assert detect_dead_code(graph) == ["m.func"]


def test_entry_point_via_main_module() -> None:
    func = _node("m.func", module="__main__")
    graph = SymbolGraph(nodes={"m.func": func})

    assert detect_entry_points(graph) == ["m.func"]
