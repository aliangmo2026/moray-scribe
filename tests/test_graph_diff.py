"""Tests for baseline-vs-current graph diffing (CI arch-check engine)."""

from __future__ import annotations

import pytest

from moray_scribe.grapher.diff import GraphDiffReport, HubShift, diff_graphs, render_diff_markdown
from moray_scribe.schema import EdgeKind, SymbolEdge, SymbolGraph, SymbolKind, SymbolNode


def _node(
    symbol_id: str,
    *,
    kind: SymbolKind = SymbolKind.FUNCTION,
    pagerank: float = 0.0,
    decorators: list[str] | None = None,
) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module=symbol_id.split(".")[0],
        file=f"{symbol_id.split('.')[0]}.py",
        lineno=1,
        end_lineno=2,
        pagerank=pagerank,
        decorators=decorators or [],
    )


def _graph(spec: dict[str, SymbolNode], edges: list[tuple[str, str]]) -> SymbolGraph:
    return SymbolGraph(
        nodes=spec,
        edges=[SymbolEdge(source=src, target=dst, kind=EdgeKind.CALLS) for src, dst in edges],
    )


def test_empty_diff_when_graphs_unchanged() -> None:
    graph = _graph(
        {"m.a": _node("m.a", pagerank=0.5), "m.b": _node("m.b", pagerank=0.3)},
        [("m.a", "m.b")],
    )

    report = diff_graphs(graph, graph)

    assert isinstance(report, GraphDiffReport)
    assert report.new_dead == []
    assert report.resolved_dead == []
    assert report.hub_shifts == []
    assert report.findings_count == 0


def test_new_dead_code_is_detected() -> None:
    baseline = _graph({"m.a": _node("m.a"), "m.b": _node("m.b")}, [("m.a", "m.b")])
    # m.lonely appears in current with no callers and no entry markers.
    current = _graph(
        {"m.a": _node("m.a"), "m.b": _node("m.b"), "m.lonely": _node("m.lonely")},
        [("m.a", "m.b")],
    )

    report = diff_graphs(baseline, current)

    assert report.new_dead == ["m.lonely"]
    assert report.resolved_dead == []


def test_new_dead_in_baseline_only_resolves() -> None:
    baseline = _graph(
        {"m.a": _node("m.a"), "m.lonely": _node("m.lonely")},
        [("m.a", "m.b2")],
    )
    current = _graph({"m.a": _node("m.a")}, [])

    report = diff_graphs(baseline, current)

    assert report.new_dead == []
    assert report.resolved_dead == ["m.lonely"]


def test_entry_points_are_never_dead() -> None:
    route = _node("web.route", decorators=['app.route("/x")'])
    baseline = _graph({"web.route": route, "web.other": _node("web.other")}, [])
    current = _graph(
        {
            "web.route": _node("web.route", decorators=['app.route("/x")']),
            "web.other": _node("web.other"),
        },
        [],
    )

    report = diff_graphs(baseline, current)

    assert report.new_dead == []


def test_hub_score_collapse_is_a_shift() -> None:
    baseline = _graph(
        {
            "m.hub": _node("m.hub", pagerank=0.40),
            "m.small": _node("m.small", pagerank=0.04),
        },
        [],
    )
    current = _graph(
        {
            "m.hub": _node("m.hub", pagerank=0.10),
            "m.small": _node("m.small", pagerank=0.03),
        },
        [],
    )

    report = diff_graphs(baseline, current, hub_top=2, hub_score_drop=0.5)

    assert len(report.hub_shifts) == 1
    shift = report.hub_shifts[0]
    assert shift.node_id == "m.hub"
    assert shift.old_rank == 1
    assert shift.new_rank == 1
    assert shift.old_score == pytest.approx(0.40)
    assert shift.new_score == pytest.approx(0.10)


def test_hub_leaving_top_n_is_a_shift() -> None:
    baseline = _graph(
        {
            "m.hub": _node("m.hub", pagerank=0.40),
            "m.a": _node("m.a", pagerank=0.30),
            "m.b": _node("m.b", pagerank=0.20),
        },
        [],
    )
    current = _graph(
        {
            "m.hub": _node("m.hub", pagerank=0.05),
            "m.a": _node("m.a", pagerank=0.30),
            "m.b": _node("m.b", pagerank=0.20),
            "m.c": _node("m.c", pagerank=0.10),
        },
        [],
    )

    report = diff_graphs(baseline, current, hub_top=2, hub_score_drop=0.5)

    by_node = {shift.node_id: shift for shift in report.hub_shifts}
    assert by_node["m.hub"].old_rank == 1
    assert by_node["m.hub"].new_rank is None


def test_new_hub_is_reported() -> None:
    baseline = _graph({"m.a": _node("m.a", pagerank=0.5)}, [])
    current = _graph(
        {
            "m.a": _node("m.a", pagerank=0.4),
            "m.newhub": _node("m.newhub", pagerank=0.45),
        },
        [],
    )

    report = diff_graphs(baseline, current, hub_top=1)

    new_hubs = [shift for shift in report.hub_shifts if shift.old_rank is None]
    assert [shift.node_id for shift in new_hubs] == ["m.newhub"]


def test_mild_hub_drift_is_not_a_shift() -> None:
    baseline = _graph({"m.hub": _node("m.hub", pagerank=0.40)}, [])
    current = _graph({"m.hub": _node("m.hub", pagerank=0.34)}, [])

    report = diff_graphs(baseline, current, hub_top=1, hub_score_drop=0.5)

    assert report.hub_shifts == []


def test_findings_count_and_report_render() -> None:
    baseline = _graph({"m.a": _node("m.a", pagerank=0.5)}, [])
    current = _graph(
        {
            "m.a": _node("m.a", pagerank=0.1),
            "m.dead_new": _node("m.dead_new"),
        },
        [],
    )
    report = diff_graphs(baseline, current, hub_top=1)

    assert report.findings_count >= 2
    text = render_diff_markdown(report, project="demo")

    assert "新增死代码" in text
    assert "m.dead_new" in text
    assert "m.a" in text
    assert "demo" in text


def test_report_models_are_strict() -> None:
    with pytest.raises(ValueError):
        HubShift(node_id="", old_rank=None, new_rank=None, old_score=-1.0, new_score=0.0)
