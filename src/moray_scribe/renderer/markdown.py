"""Render an architecture draft plus engine statistics as ARCHITECTURE.md."""

from __future__ import annotations

from moray_scribe import __version__
from moray_scribe.schema import ArchitectureDraft, SymbolGraph, SymbolKind

from .mermaid import diagram_to_mermaid_fence

_HUB_LIMIT = 20
_DEAD_LIMIT = 30
_SYMBOL_LIMIT = 10


def _symbol_list(symbol_ids: list[str]) -> str:
    """Render up to ten symbol ids, truncating with an ellipsis note."""
    if not symbol_ids:
        return "无"
    shown = ", ".join(f"`{sid}`" for sid in symbol_ids[:_SYMBOL_LIMIT])
    if len(symbol_ids) > _SYMBOL_LIMIT:
        shown += f" … 等 {len(symbol_ids)} 个"
    return shown


def _project_stats(graph: SymbolGraph) -> dict[str, object]:
    """Compute deterministic statistics from the symbol graph."""
    nodes = list(graph.nodes.values())
    files = {node.file for node in nodes}
    symbols = [node for node in nodes if node.kind is not SymbolKind.MODULE]
    modules = [node for node in nodes if node.kind is SymbolKind.MODULE]
    n_nodes = len(nodes)
    if n_nodes > 1:
        density: object = len(graph.edges) / (n_nodes * (n_nodes - 1))
    else:
        density = "N/A"
    return {
        "files": len(files),
        "symbols": len(symbols),
        "modules": len(modules),
        "edges": len(graph.edges),
        "density": density,
    }


def _section_hubs(core_hubs: list[tuple[str, float]], graph: SymbolGraph) -> list[str]:
    lines = [
        "按 PageRank 排序，这些节点是全项目拓扑意义上的\"关键枢纽\"：",
        "",
        "| 排名 | 符号 | 类型 | 模块 | PageRank |",
        "|---:|---|---|---|---:|",
    ]
    if not core_hubs:
        lines.append("| — | 未识别到核心枢纽 | — | — | — |")
        return lines
    shown = core_hubs[:_HUB_LIMIT]
    for rank, (node_id, score) in enumerate(shown, start=1):
        node = graph.nodes.get(node_id)
        kind = node.kind.value if node is not None else "unknown"
        module = node.module if node is not None else ""
        lines.append(f"| {rank} | `{node_id}` | {kind} | {module} | {score:.4f} |")
    if len(core_hubs) > _HUB_LIMIT:
        lines.append(f"| … | 等 {len(core_hubs)} 个 | — | — | — |")
    return lines


def _section_dead(dead_code: list[str], graph: SymbolGraph) -> list[str]:
    lines = [
        "> ⚠️ 以下符号入度为 0、非入口、非私有工具。",
        "> 建议人工确认后**删除或标注**——不要盲目信任本列表。",
        "",
    ]
    if not dead_code:
        lines.append("未检测到死代码候选。")
        return lines
    for node_id in dead_code[:_DEAD_LIMIT]:
        node = graph.nodes.get(node_id)
        kind = node.kind.value if node is not None else "unknown"
        module = node.module if node is not None else ""
        lines.append(f"- `{node_id}`（{kind}, {module}）")
    if len(dead_code) > _DEAD_LIMIT:
        lines.append(f"- … 等 {len(dead_code)} 个")
    return lines


def _section_entries(entry_points: list[str], graph: SymbolGraph) -> list[str]:
    if not entry_points:
        return ["未识别到明显入口点。"]
    lines: list[str] = []
    for node_id in entry_points:
        node = graph.nodes.get(node_id)
        kind = node.kind.value if node is not None else "unknown"
        module = node.module if node is not None else ""
        lines.append(f"- `{node_id}`（{kind}, {module}）")
    return lines


def draft_to_markdown(
    draft: ArchitectureDraft,
    *,
    graph: SymbolGraph,
    full_graph: SymbolGraph,
    known_gaps: list[str],
    dead_code: list[str],
    entry_points: list[str],
    core_hubs: list[tuple[str, float]],
    project_name: str,
) -> str:
    """Generate the complete ARCHITECTURE.md content.

    AI reasoning comes from ``draft`` (generated over the pruned ``graph``);
    engine statistics (hubs, dead code, entry points, appendix stats) come from
    ``full_graph`` so the numbers reflect the whole project, not the pruned
    subset. No timestamps are emitted.
    """
    lines: list[str] = [f"# {project_name} · 架构文档", ""]

    lines += [
        "> 本文件由 moray-scribe 生成。**AI 推理**与**引擎统计**两部分显式分栏，",
        "> 前者由大模型基于符号图推理，可能有推断误差；后者由静态分析确定性计算，可复现。",
        "",
        "---",
        "",
        "## 一、概览【AI 推理】",
        "",
        draft.summary or "_（模型未提供概览）_",
        "",
    ]
    if draft.risks:
        lines.append("**风险点：**")
        lines.extend(f"- {risk}" for risk in draft.risks)
        lines.append("")
    if draft.recommendations:
        lines.append("**建议：**")
        lines.extend(f"- {rec}" for rec in draft.recommendations)
        lines.append("")

    lines += ["---", "", "## 二、系统架构图【AI 推理】", ""]

    flowcharts = [d for d in draft.diagrams if d.kind.value == "flowchart"]
    class_diagrams = [d for d in draft.diagrams if d.kind.value == "classDiagram"]

    lines.append("### 主图（architecture）")
    lines.append("")
    if flowcharts:
        for diagram in flowcharts:
            lines.append(diagram_to_mermaid_fence(diagram))
            lines.append("")
    else:
        lines.append("_（未生成主图）_")
        lines.append("")

    lines.append("### 细节图（可选）")
    lines.append("")
    if class_diagrams:
        for diagram in class_diagrams:
            lines.append(diagram_to_mermaid_fence(diagram))
            lines.append("")
    else:
        lines.append("_（本次未生成细节图）_")
        lines.append("")

    lines += ["---", "", "## 三、模块详解【AI 推理】", ""]
    if not draft.modules:
        lines.append("_（模型未提供模块说明）_")
        lines.append("")
    for module in draft.modules:
        lines.append(f"### `{module.module}`")
        lines.append("")
        responsibilities = "；".join(module.responsibilities) if module.responsibilities else "无"
        lines.append(f"- **职责**：{responsibilities}")
        lines.append(f"- **关键符号**：{_symbol_list(module.symbol_ids)}")
        lines.append(f"- **风险**：{module.risk}")
        lines.append("")

    lines += [
        "---",
        "",
        "## 四、核心枢纽【引擎统计】",
        "",
    ]
    lines += _section_hubs(core_hubs, full_graph)
    lines += ["", "---", "", "## 五、死代码候选【引擎统计】", ""]
    lines += _section_dead(dead_code, full_graph)
    lines += ["", "---", "", "## 六、入口点【引擎统计】", ""]
    lines += _section_entries(entry_points, full_graph)
    lines += [
        "",
        "---",
        "",
        "## 七、已知缺口【引擎统计 + AI 提示】",
        "",
        "> 以下关系 scanner 无法百分百确认，AI 推理中也未覆盖。",
        "> 人工审查时请重点确认这些位置。",
        "",
    ]
    if known_gaps:
        lines.extend(f"- {gap}" for gap in known_gaps)
    else:
        lines.append("未发现已知缺口。")
    lines.append("")

    stats = _project_stats(full_graph)
    density = stats["density"]
    density_text = f"{density:.4f}" if isinstance(density, float) else str(density)
    lines += [
        "---",
        "",
        "## 附录：项目统计【引擎统计】",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 分析文件数 | {stats['files']} |",
        f"| 符号总数 | {stats['symbols']} |",
        f"| 模块数 | {stats['modules']} |",
        f"| 边总数 | {stats['edges']} |",
        f"| 图密度 | {density_text} |",
        "",
        f"*生成工具：moray-scribe v{__version__}*",
    ]
    return "\n".join(lines)
