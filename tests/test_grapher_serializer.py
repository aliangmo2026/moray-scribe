"""Unit tests for prompt-view serialization."""

from __future__ import annotations

from moray_scribe.grapher import to_prompt_view
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
    docstring: str | None = None,
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
        docstring=docstring,
        is_dead_candidate=is_dead_candidate,
    )


def _sample_graph() -> SymbolGraph:
    live = _node("m.live", docstring="A live function.")
    dead = _node("m.dead", is_dead_candidate=True)
    other = _node("m.other", kind=SymbolKind.CLASS)
    return SymbolGraph(
        nodes={"m.live": live, "m.dead": dead, "m.other": other},
        edges=[
            SymbolEdge(source="m.other", target="m.live", kind=EdgeKind.CALLS),
            SymbolEdge(source="m.other", target="m.dead", kind=EdgeKind.CALLS),
        ],
    )


def test_prompt_view_shape() -> None:
    view = to_prompt_view(_sample_graph())

    assert set(view) == {"n", "e"}
    node = view["n"][0]
    assert set(node) == {"i", "k", "n", "m", "h", "e", "d"}
    edge = view["e"][0]
    assert set(edge) == {"s", "d", "k", "w"}
    assert node["i"] == "m.live"
    assert node["k"] == "function"
    assert node["d"] == "A live function."


def test_prompt_view_drops_dead() -> None:
    view = to_prompt_view(_sample_graph())

    ids = {node["i"] for node in view["n"]}
    assert "m.dead" not in ids
    assert "m.live" in ids
    # Edges touching the dropped node are removed too.
    for edge in view["e"]:
        assert "m.dead" not in (edge["s"], edge["d"])


def test_prompt_view_include_dead() -> None:
    view = to_prompt_view(_sample_graph(), include_dead=True)

    ids = {node["i"] for node in view["n"]}
    assert "m.dead" in ids


def test_docstring_truncated() -> None:
    long_text = "x" * 200
    graph = SymbolGraph(
        nodes={"m.f": _node("m.f", docstring=long_text)},
    )

    view = to_prompt_view(graph, max_description_chars=80)

    assert view["n"][0]["d"] == "x" * 80


def test_empty_graph_view() -> None:
    view = to_prompt_view(SymbolGraph())

    assert view == {"n": [], "e": []}
