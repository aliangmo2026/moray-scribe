"""Render an architecture draft plus engine statistics as ARCHITECTURE.md."""

from __future__ import annotations

from moray_scribe import __version__
from moray_scribe.describer.inferencer import InferenceReport
from moray_scribe.schema import (
    ArchEdgeKind,
    ArchitectureDraft,
    DiagramKind,
    Direction,
    EdgeKind,
    EdgeStyle,
    MermaidDiagram,
    MermaidEdge,
    MermaidNode,
    NodeKind,
    SymbolGraph,
    SymbolKind,
)

from .mermaid import diagram_to_mermaid_fence

_HUB_LIMIT = 20
_DEAD_LIMIT = 30
_SYMBOL_LIMIT = 10
_INFERRED_LIMIT = 40
_GAP_LIMIT = 40
_ROUTE_LIMIT = 60
_DATAFLOW_EDGE_LIMIT = 30


def _safe_mermaid_id(symbol_id: str, used: set[str]) -> str:
    """Deterministically map an arbitrary symbol id to a Mermaid-safe node id.

    Every character outside ``[A-Za-z0-9_]`` becomes ``_`` (so dotted ids stay
    readable: ``app.auth.login`` -> ``app_auth_login``); a non-letter first
    character gains an ``N_`` prefix; collisions get numeric suffixes in sorted
    input order, so the same graph always renders the same ids.
    """
    cleaned = "".join(
        char
        if ("a" <= char <= "z") or ("A" <= char <= "Z") or ("0" <= char <= "9") or char == "_"
        else "_"
        for char in symbol_id
    ).strip("_")
    if not cleaned:
        cleaned = "node"
    if not (("a" <= cleaned[0] <= "z") or ("A" <= cleaned[0] <= "Z")):
        cleaned = f"N_{cleaned}"
    candidate = cleaned
    counter = 1
    while candidate in used:
        candidate = f"{cleaned}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def _section_routes(graph: SymbolGraph) -> list[str]:
    """Deterministic HTTP route table from the full graph's route metadata."""
    routes = sorted(
        (node for node in graph.nodes.values() if node.route is not None),
        key=lambda node: (node.route.path, node.id),  # type: ignore[union-attr]
    )
    if not routes:
        return ["未检测到 HTTP 路由（非 Web 项目，或路由框架不在识别范围内）。"]
    lines = [
        "扫描器从路由装饰器（Flask/FastAPI/Django 风格）中确定性提取，非 AI 推断：",
        "",
        "| 方法 | 路径 | 处理函数 | 模块 |",
        "|---|---|---|---|",
    ]
    for node in routes[:_ROUTE_LIMIT]:
        assert node.route is not None  # for type checkers; filtered above
        methods = "/".join(node.route.methods)
        lines.append(f"| {methods} | `{node.route.path}` | `{node.id}` | {node.module} |")
    if len(routes) > _ROUTE_LIMIT:
        lines.append(f"| … | 等 {len(routes)} 条路由 | | |")
    return lines


def _section_dataflow(graph: SymbolGraph) -> list[str]:
    """Deterministic ORM data-flow section: Mermaid figure plus edge table."""
    edges = sorted(
        (edge for edge in graph.edges if edge.kind is EdgeKind.DATA_FLOW),
        key=lambda edge: (edge.source, edge.target),
    )
    if not edges:
        return [
            "未检测到静态可确认的 ORM/会话数据流。",
            "悬空的数据库调用（`db.session...` 等）会进入 `--infer` 动态推断池，",
            "其结果见「AI 动态推断」一节。",
        ]
    shown = edges[:_DATAFLOW_EDGE_LIMIT]
    used_ids: set[str] = set()
    id_by_symbol: dict[str, str] = {}
    involved: list[str] = []
    for edge in shown:
        for endpoint in (edge.source, edge.target):
            if endpoint not in id_by_symbol and endpoint in graph.nodes:
                id_by_symbol[endpoint] = _safe_mermaid_id(endpoint, used_ids)
                involved.append(endpoint)

    diagram_nodes: list[MermaidNode] = []
    for symbol_id in sorted(involved):
        node = graph.nodes[symbol_id]
        if "orm_model" in node.tags:
            kind = NodeKind.DATABASE
        elif node.kind is SymbolKind.MODULE:
            kind = NodeKind.MODULE
        elif node.kind is SymbolKind.CLASS:
            kind = NodeKind.CLASS
        else:
            kind = NodeKind.FUNCTION
        diagram_nodes.append(
            MermaidNode(
                id=id_by_symbol[symbol_id],
                label=node.name,
                kind=kind,
                module=node.module,
                description=node.module,
            )
        )
    diagram_edges = [
        MermaidEdge(
            source=id_by_symbol[edge.source],
            target=id_by_symbol[edge.target],
            label="数据流",
            style=EdgeStyle.THICK,
        )
        for edge in shown
    ]
    diagram = MermaidDiagram(
        kind=DiagramKind.FLOWCHART,
        direction=Direction.TD,
        title="数据流向图（引擎统计，确定性生成）",
        nodes=diagram_nodes,
        edges=diagram_edges,
    )

    lines = [
        "> 粗箭头（`==>`）= 静态分析确认的数据流向（ORM 查询/会话提交/模型写入）。",
        "> 橙色数据库节点 = 识别出的 ORM 模型类。此图为确定性生成，非 AI 推断。",
        "",
        diagram_to_mermaid_fence(diagram),
        "",
        "涉及的数据流明细：",
        "",
        "| 调用方 | 数据目标 | 类型 |",
        "|---|---|---|",
    ]
    for edge in shown:
        target = graph.nodes.get(edge.target)
        if target is not None and "orm_model" in target.tags:
            kind_text = "ORM 模型"
        else:
            kind_text = target.kind.value if target else "?"
        lines.append(f"| `{edge.source}` | `{edge.target}` | {kind_text} |")
    if len(edges) > _DATAFLOW_EDGE_LIMIT:
        lines.append(f"| … | 等 {len(edges)} 条数据流 | |")
    return lines


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


def _section_inference_diagram(inference: InferenceReport) -> list[str]:
    """Deterministic Mermaid view of the merged AI-inferred edges.

    The main architecture figure is authored by the model; when its nodes do
    not carry ``module``/``symbol_ids`` the engine cannot attach inferred
    arrows to them (``drew 0 inferred edge(s)``). This small figure is always
    correct: it is rendered straight from the merged edge list, so an
    ARCHITECTURE.md produced with ``--infer`` always contains a dashed
    ``[AI推断]`` Mermaid diagram.
    """
    merged = sorted(inference.merged, key=lambda item: (item.src, item.dst))[:20]
    if not merged:
        return []

    used_ids: set[str] = set()
    id_by_symbol: dict[str, str] = {}
    for edge in merged:
        for symbol_id in (edge.src, edge.dst):
            if symbol_id not in id_by_symbol:
                id_by_symbol[symbol_id] = _safe_mermaid_id(symbol_id, used_ids)

    nodes = [
        MermaidNode(
            id=id_by_symbol[symbol_id],
            label=symbol_id.rsplit(".", 1)[-1],
            kind=NodeKind.FUNCTION,
            description=symbol_id,
        )
        for symbol_id in sorted(id_by_symbol)
    ]
    edges = [
        MermaidEdge(
            source=id_by_symbol[edge.src],
            target=id_by_symbol[edge.dst],
            label="[AI推断]",
            kind=ArchEdgeKind.CALLS,
            style=EdgeStyle.DASHED,
            inferred=True,
        )
        for edge in merged
    ]
    diagram = MermaidDiagram(
        kind=DiagramKind.FLOWCHART,
        direction=Direction.LR,
        title="AI 推断补全的调用关系",
        nodes=nodes,
        edges=edges,
    )
    lines = [
        f"**{diagram.title}** —— 红色虚线 = 大模型对悬空调用的补全，需人工复核：",
        "",
        diagram_to_mermaid_fence(diagram),
    ]
    if len(inference.merged) > len(merged):
        lines.append(f"_（仅展示前 {len(merged)} 条，共 {len(inference.merged)} 条）_")
    return lines


def _section_inference(inference: InferenceReport | None) -> list[str]:
    """Render the AI dynamic-inference section (§1b), or a not-run note."""
    lines = [
        "> **实线是静态分析确认的拓扑，虚线（`-.->`）是 AI 推断补全。**",
        "> 推断边来自大模型对悬空调用的分析，已合并的均标注 `[AI推断]`，",
        "> 可能有误，请人工复核后再采信。",
        "",
    ]
    if inference is None:
        lines.append("本次运行未启用 AI 动态推断（`--infer` 可开启）。")
        return lines
    if not inference.merged:
        lines.append(
            f"候选悬空调用 {inference.candidates_total} 个，送入模型 "
            f"{inference.selected} 个，但没有任何高置信度推断边被合并。"
        )
    else:
        lines.append(
            f"以下 **{len(inference.merged)}** 条高置信度推断边已合并进符号图，"
            "并在架构图中以虚线渲染："
        )
        lines.append("")
        lines += ["| 调用方 | 被调方（推断） | 原始调用 | 依据 |", "|---|---|---|---|"]
        for edge in sorted(inference.merged, key=lambda item: (item.src, item.dst))[
            :_INFERRED_LIMIT
        ]:
            callee = f"`{edge.callee}`" if edge.callee else "—"
            lines.append(f"| `{edge.src}` | `{edge.dst}` | {callee} | {edge.reason} |")
        if len(inference.merged) > _INFERRED_LIMIT:
            lines.append(f"| … | 等 {len(inference.merged)} 条 | | |")
        lines.append("")
        lines += _section_inference_diagram(inference)
    lines.append("")
    lines.append(
        f"推断统计：候选 {inference.candidates_total}，送入模型 {inference.selected}，"
        f"请求 {inference.requests} 次，token 消耗约 "
        f"{inference.prompt_tokens}(入) + {inference.completion_tokens}(出)，"
        f"无效回答 {inference.rejected}。"
    )
    if inference.gaps:
        lines.append("")
        gap_count = len(inference.gaps)
        lines.append(f"**中低置信度推断（未合图，共 {gap_count} 条，已列入「已知缺口」）：**")
        for edge in sorted(inference.gaps, key=lambda item: (item.src, item.dst))[:_GAP_LIMIT]:
            callee = f"（`{edge.callee}`）" if edge.callee else ""
            lines.append(f"- `{edge.src}`{callee} → `{edge.dst}`：{edge.reason}")
        if len(inference.gaps) > _GAP_LIMIT:
            lines.append(f"- … 等 {len(inference.gaps)} 条")
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
    inference: InferenceReport | None = None,
) -> str:
    """Generate the complete ARCHITECTURE.md content.

    AI reasoning comes from ``draft`` (generated over the pruned ``graph``);
    engine statistics (hubs, dead code, entry points, appendix stats) come from
    ``full_graph`` so the numbers reflect the whole project, not the pruned
    subset. ``inference`` adds the dynamic-inference section when the stage
    ran. No timestamps are emitted.
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
    lines += ["", "---", "", "## 七、API 路由表【引擎统计】", ""]
    lines += _section_routes(full_graph)
    lines += ["", "---", "", "## 八、数据流向图【引擎统计】", ""]
    lines += _section_dataflow(full_graph)
    lines += [
        "",
        "---",
        "",
        "## 九、已知缺口【引擎统计 + AI 提示】",
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

    lines += ["---", "", "## 十、AI 动态推断【AI 推断】", ""]
    lines += _section_inference(inference)
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
