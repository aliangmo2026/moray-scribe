"""Unit tests for ARCHITECTURE.md generation."""

from __future__ import annotations

import hashlib

from moray_scribe.renderer import draft_to_markdown
from moray_scribe.schema import (
    EdgeKind,
    SymbolEdge,
    SymbolGraph,
    SymbolKind,
    SymbolNode,
)


def _render(draft, graph, **overrides):
    kwargs = {
        "graph": graph,
        "full_graph": graph,
        "known_gaps": [],
        "dead_code": [],
        "entry_points": [],
        "core_hubs": [],
        "project_name": "sample",
    }
    kwargs.update(overrides)
    return draft_to_markdown(draft, **kwargs)


def _node(symbol_id: str, *, kind: SymbolKind, file: str) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module=symbol_id.split(".")[0],
        file=file,
        lineno=1,
        end_lineno=2,
    )


def _stats_graph() -> SymbolGraph:
    nodes = {
        "m": _node("m", kind=SymbolKind.MODULE, file="__init__.py"),
        "m.a": _node("m.a", kind=SymbolKind.FUNCTION, file="a.py"),
        "m.b": _node("m.b", kind=SymbolKind.CLASS, file="b.py"),
    }
    return SymbolGraph(
        nodes=nodes,
        edges=[SymbolEdge(source="m.a", target="m.b", kind=EdgeKind.CALLS)],
    )


def test_markdown_has_all_sections(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph)

    for heading in (
        "## 一、概览【AI 推理】",
        "## 二、系统架构图【AI 推理】",
        "## 三、模块详解【AI 推理】",
        "## 四、核心枢纽【引擎统计】",
        "## 五、死代码候选【引擎统计】",
        "## 六、入口点【引擎统计】",
        "## 七、已知缺口【引擎统计 + AI 提示】",
        "## 附录：项目统计【引擎统计】",
    ):
        assert heading in md


def test_ai_vs_engine_sections_distinct(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph)

    assert md.count("【AI 推理】") >= 2
    assert md.count("【引擎统计】") >= 3


def test_module_detail_renders_risks(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph)

    assert "### `core`" in md
    assert "medium" in md


def test_module_detail_empty_responsibilities(sample_rendered_graph) -> None:
    from moray_scribe.schema import ArchitectureDraft

    draft = ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": "A summary that is long enough to pass the fifty character rule easily.",
            "modules": [{"module": "core", "symbol_ids": []}],
        }
    )

    md = _render(draft, sample_rendered_graph)

    assert "**职责**：无" in md


def test_core_hubs_sorted_by_pagerank(sample_draft, sample_rendered_graph) -> None:
    hubs = [("a", 0.5), ("b", 0.3), ("c", 0.1)]

    md = _render(sample_draft, sample_rendered_graph, core_hubs=hubs)

    first = md.index("| 1 | `a`")
    second = md.index("| 2 | `b`")
    third = md.index("| 3 | `c`")
    assert first < second < third


def test_core_hubs_truncated_at_20(sample_draft, sample_rendered_graph) -> None:
    hubs = [(f"n{i}", 1.0 - i) for i in range(25)]

    md = _render(sample_draft, sample_rendered_graph, core_hubs=hubs)

    assert "等 25 个" in md
    assert "| 20 |" in md
    assert "| 21 |" not in md


def test_dead_code_empty_message(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph, dead_code=[])

    assert "未检测到死代码候选。" in md


def test_markdown_determinism(sample_draft, sample_rendered_graph) -> None:
    first = _render(sample_draft, sample_rendered_graph)
    second = _render(sample_draft, sample_rendered_graph)

    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()


def test_project_stats_file_count(sample_draft) -> None:
    graph = _stats_graph()

    md = _render(sample_draft, graph)

    # Three nodes with three distinct files (__init__.py, a.py, b.py).
    assert "| 分析文件数 | 3 |" in md


def test_project_stats_excludes_virtual_module(sample_draft) -> None:
    graph = _stats_graph()

    md = _render(sample_draft, graph)

    # 3 nodes total, 1 is the virtual MODULE -> 2 symbols.
    assert "| 符号总数 | 2 |" in md
    assert "| 模块数 | 1 |" in md
    assert "| 边总数 | 1 |" in md


def test_class_diagram_optional_empty(sample_rendered_graph) -> None:
    from moray_scribe.schema import ArchitectureDraft

    draft = ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": "A summary that is long enough to pass the fifty character rule easily.",
            "diagrams": [
                {
                    "kind": "flowchart",
                    "title": "Main",
                    "nodes": [{"id": "A", "label": "A"}],
                    "edges": [],
                }
            ],
        }
    )

    md = _render(draft, sample_rendered_graph)

    assert "_（本次未生成细节图）_" in md


def test_markdown_no_timestamp(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph)

    assert "2025-" not in md
    assert "2026-" not in md
    assert "2024-" not in md


def test_known_gaps_rendered(sample_draft, sample_rendered_graph) -> None:
    md = _render(sample_draft, sample_rendered_graph, known_gaps=["gap one", "gap two"])

    # The renderer adds the "- " bullet prefix itself.
    assert "- gap one" in md
    assert "- gap two" in md


def test_markdown_stats_use_full_graph(sample_draft) -> None:
    a = _node("m.a", kind=SymbolKind.FUNCTION, file="a.py")
    b = _node("m.b", kind=SymbolKind.FUNCTION, file="b.py")
    full = SymbolGraph(nodes={"m.a": a, "m.b": b})
    pruned = SymbolGraph(nodes={"m.a": a})

    md = _render(sample_draft, pruned, full_graph=full)

    # Stats come from the FULL graph (2 files / 2 symbols), not the pruned one.
    assert "| 分析文件数 | 2 |" in md
    assert "| 符号总数 | 2 |" in md


def test_markdown_hubs_from_full_graph(sample_draft) -> None:
    a = _node("m.a", kind=SymbolKind.CLASS, file="a.py")
    b = _node("m.b", kind=SymbolKind.CLASS, file="b.py")
    full = SymbolGraph(nodes={"m.a": a, "m.b": b})
    pruned = SymbolGraph(nodes={"m.b": b})

    md = _render(
        sample_draft, pruned, full_graph=full, core_hubs=[("m.a", 0.5), ("m.b", 0.1)]
    )

    # m.a exists only in full_graph; its row must still render.
    assert "| 1 | `m.a` | class | m | 0.5000 |" in md
