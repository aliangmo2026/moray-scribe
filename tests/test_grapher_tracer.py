"""Tests for entry-point call-chain tracing (sequence-diagram stage input)."""

from __future__ import annotations

import pytest

from moray_scribe.grapher.tracer import (
    CallChain,
    ChainStep,
    chain_step_arrows,
    trace_call_chains,
)
from moray_scribe.schema import EdgeKind, SymbolEdge, SymbolGraph, SymbolKind, SymbolNode


def _node(symbol_id: str, kind: SymbolKind = SymbolKind.FUNCTION) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module=symbol_id.split(".")[0],
        file=f"{symbol_id.split('.')[0]}.py",
        lineno=1,
        end_lineno=2,
    )


def _graph(
    nodes: list[str],
    edges: list[tuple[str, str, EdgeKind]],
    *,
    pageranks: dict[str, float] | None = None,
) -> SymbolGraph:
    graph = SymbolGraph(
        nodes={node_id: _node(node_id) for node_id in nodes},
        edges=[
            SymbolEdge(source=src, target=dst, kind=kind) for src, dst, kind in edges
        ],
    )
    for node_id, score in (pageranks or {}).items():
        graph.nodes[node_id].pagerank = score
    return graph


def test_empty_graph_yields_no_chains() -> None:
    assert trace_call_chains(SymbolGraph()) == []


def test_chain_follows_calls_in_pagerank_order() -> None:
    graph = _graph(
        ["app.main", "app.a", "app.b", "app.c"],
        [
            ("app.main", "app.a", EdgeKind.CALLS),
            ("app.main", "app.b", EdgeKind.CALLS),
            ("app.b", "app.c", EdgeKind.CALLS),
        ],
        pageranks={"app.main": 0.6, "app.b": 0.3, "app.a": 0.05, "app.c": 0.05},
    )
    graph.nodes["app.main"].is_entry = True

    chains = trace_call_chains(graph)

    # app.b outranks app.a, so its branch is traced first and carries deeper flow.
    assert chains[0].entry == "app.main"
    assert [(step.src, step.dst) for step in chains[0].steps] == [
        ("app.main", "app.b"),
        ("app.b", "app.c"),
    ]
    assert chains[1].entry == "app.main"
    assert [(step.src, step.dst) for step in chains[1].steps] == [("app.main", "app.a")]


def test_entry_used_even_without_is_entry_flag() -> None:
    """detect_entry_points-style data (route decorators) enters via names too."""
    graph = _graph(
        ["web.login", "web.db.check"],
        [("web.login", "web.db.check", EdgeKind.CALLS)],
        pageranks={"web.login": 0.4, "web.db.check": 0.1},
    )
    graph.nodes["web.login"].route = None
    graph.nodes["web.login"].decorators = ["app.route(\"/login\")"]

    chains = trace_call_chains(graph)

    assert chains
    assert chains[0].entry == "web.login"
    assert [(step.src, step.dst) for step in chains[0].steps] == [
        ("web.login", "web.db.check")
    ]


def test_depth_limit_truncates_and_marks() -> None:
    graph = _graph(
        ["e", "a", "b", "c", "d"],
        [
            ("e", "a", EdgeKind.CALLS),
            ("a", "b", EdgeKind.CALLS),
            ("b", "c", EdgeKind.CALLS),
            ("c", "d", EdgeKind.CALLS),
        ],
        pageranks={"e": 0.5, "a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1},
    )
    graph.nodes["e"].is_entry = True

    chains = trace_call_chains(graph, max_depth=2)

    assert len(chains) == 1
    assert chains[0].truncated is True
    assert [step.dst for step in chains[0].steps] == ["a", "b"]


def test_cycles_are_not_followed() -> None:
    graph = _graph(
        ["e", "a", "b"],
        [
            ("e", "a", EdgeKind.CALLS),
            ("a", "b", EdgeKind.CALLS),
            ("b", "a", EdgeKind.CALLS),
        ],
        pageranks={"e": 0.5, "a": 0.3, "b": 0.2},
    )
    graph.nodes["e"].is_entry = True

    chains = trace_call_chains(graph, max_depth=6)

    assert len(chains) == 1
    assert [(step.src, step.dst) for step in chains[0].steps] == [
        ("e", "a"),
        ("a", "b"),
    ]
    assert chains[0].truncated is False


def test_containment_edges_are_not_traversed() -> None:
    graph = _graph(
        ["m", "m.f"],
        [
            ("m", "m.f", EdgeKind.CONTAINS),
            ("m", "m.f", EdgeKind.CALLS),
        ],
        pageranks={"m": 0.4, "m.f": 0.1},
    )
    graph.nodes["m"].is_entry = True

    chains = trace_call_chains(graph)

    assert len(chains) == 1
    assert chains[0].steps[0].kind == EdgeKind.CALLS.value


def test_instantiates_is_traversed() -> None:
    graph = _graph(
        ["svc.run", "core.Engine"],
        [("svc.run", "core.Engine", EdgeKind.INSTANTIATES)],
        pageranks={"svc.run": 0.4, "core.Engine": 0.2},
    )
    graph.nodes["svc.run"].is_entry = True

    chains = trace_call_chains(graph)

    assert chains[0].steps[0].kind == EdgeKind.INSTANTIATES.value


def test_fallback_entries_when_no_entry_flags() -> None:
    """Without entry markers, the highest-pagerank callers seed the trace."""
    graph = _graph(
        ["x.alpha", "x.beta", "x.gamma"],
        [
            ("x.alpha", "x.beta", EdgeKind.CALLS),
            ("x.beta", "x.gamma", EdgeKind.CALLS),
        ],
        pageranks={"x.alpha": 0.5, "x.beta": 0.3, "x.gamma": 0.1},
    )

    chains = trace_call_chains(graph)

    assert chains
    assert chains[0].entry == "x.alpha"


def test_per_entry_and_global_caps() -> None:
    edges = [("e", f"leaf{i}", EdgeKind.CALLS) for i in range(5)]
    graph = _graph(
        ["e", *(f"leaf{i}" for i in range(5))],
        edges,
        pageranks={"e": 0.6, **{f"leaf{i}": 0.1 for i in range(5)}},
    )
    graph.nodes["e"].is_entry = True

    chains = trace_call_chains(graph, chains_per_entry=2)
    assert len(chains) == 2

    many = trace_call_chains(graph, chains_per_entry=5, max_chains=3)
    assert len(many) == 3


def test_dedup_and_determinism() -> None:
    edges = [
        ("e", "a", EdgeKind.CALLS),
        ("a", "b", EdgeKind.CALLS),
        ("e", "b", EdgeKind.CALLS),
    ]
    graph = _graph(
        ["e", "a", "b"],
        edges,
        pageranks={"e": 0.6, "a": 0.2, "b": 0.1},
    )
    graph.nodes["e"].is_entry = True

    first = trace_call_chains(graph)
    second = trace_call_chains(graph)

    keys = {(chain.entry, tuple((s.src, s.dst) for s in chain.steps)) for chain in first}
    assert len(keys) == len(first)
    assert first == second


def test_steps_carry_first_lineno() -> None:
    graph = SymbolGraph(
        nodes={"e": _node("e"), "a": _node("a")},
        edges=[SymbolEdge(source="e", target="a", kind=EdgeKind.CALLS, linenos=[7, 9])],
    )
    graph.nodes["e"].is_entry = True

    chains = trace_call_chains(graph)

    assert chains[0].steps[0].lineno == 7


def test_chain_models_are_strict() -> None:
    with pytest.raises(ValueError):
        ChainStep(src="a", dst="a", kind="nope", depth=0)
    with pytest.raises(ValueError):
        CallChain(entry="e", steps=[])


def test_chain_step_arrows_renderer_helper() -> None:
    sync = ChainStep(src="a", dst="b", kind="calls", depth=0)
    init = ChainStep(src="a", dst="b", kind="instantiates", depth=0)

    assert chain_step_arrows(sync) == ("->>", "调用")
    assert chain_step_arrows(init) == ("->>", "实例化")
