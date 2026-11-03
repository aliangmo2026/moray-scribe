"""Baseline-vs-current graph diff for CI architecture checks.

The engine behind ``moray-scribe check`` (and the GitHub Action workflow):
compare a baseline ``pruned-graph.json`` (e.g. the PR's merge-base) against
the current one and report

- **new dead code** — definitions that are unreachable now but were not in
  the baseline (a PR introducing an uncalled public function),
- **resolved dead code** — the reverse (good news, shown for context),
- **hub shifts** — a node leaving the top-N PageRank, collapsing in score by
  more than the threshold, or entering the top-N for the first time.

Both graphs must come from ``moray-scribe graph`` so PageRank is stamped on
the nodes; dead-code detection is recomputed live (never trusting flags) so
older baselines stay comparable.
"""

from __future__ import annotations

from pydantic import Field

from moray_scribe.schema import StrictModel, SymbolGraph

from .analyzer import detect_dead_code

# Nodes whose score collapses this much between top-N ranks raise an alarm.
_DEFAULT_HUB_TOP = 10
_DEFAULT_HUB_SCORE_DROP = 0.5


class HubShift(StrictModel):
    """One hub node whose rank or score moved dramatically."""

    node_id: str = Field(min_length=1, description="Symbol id of the hub.")
    old_rank: int | None = Field(description="Rank in the baseline top-N; None when absent.")
    new_rank: int | None = Field(description="Rank in the current top-N; None when gone.")
    old_score: float = Field(ge=0.0, description="Baseline PageRank score.")
    new_score: float = Field(ge=0.0, description="Current PageRank score.")


class GraphDiffReport(StrictModel):
    """Outcome of one baseline comparison."""

    baseline_nodes: int = Field(ge=0, description="Baseline graph node count.")
    current_nodes: int = Field(ge=0, description="Current graph node count.")
    new_dead: list[str] = Field(
        default_factory=list,
        description="Dead-code candidates present now but not in the baseline.",
    )
    resolved_dead: list[str] = Field(
        default_factory=list,
        description="Previously dead candidates that are now referenced.",
    )
    hub_shifts: list[HubShift] = Field(
        default_factory=list,
        description="Top-N hubs that left, collapsed or newly appeared.",
    )

    @property
    def findings_count(self) -> int:
        """Number of alert-worthy findings (new dead + hub shifts)."""
        return len(self.new_dead) + len(self.hub_shifts)


def _top_hubs(graph: SymbolGraph, hub_top: int) -> dict[str, int]:
    """Map node id -> 1-based PageRank rank for the top ``hub_top`` nodes."""
    ranked = sorted(
        graph.nodes.values(),
        key=lambda node: (-node.pagerank, node.id),
    )
    return {node.id: index for index, node in enumerate(ranked[: max(0, hub_top)], start=1)}


def diff_graphs(
    baseline: SymbolGraph,
    current: SymbolGraph,
    *,
    hub_top: int = _DEFAULT_HUB_TOP,
    hub_score_drop: float = _DEFAULT_HUB_SCORE_DROP,
) -> GraphDiffReport:
    """Diff two graphs; neither input is mutated.

    Dead-code sets are recomputed with :func:`detect_dead_code` on both sides,
    so a baseline exported by an older version stays comparable. Hub shifts
    consider the union of both top-N sets; a shift is reported when a node
    leaves the list, its score collapses past ``hub_score_drop`` (relative,
    scored against the baseline), or it enters the list for the first time.
    """
    baseline_dead = set(detect_dead_code(baseline))
    current_dead = set(detect_dead_code(current))
    new_dead = sorted(current_dead - baseline_dead)
    resolved_dead = sorted(baseline_dead - current_dead)

    old_ranks = _top_hubs(baseline, hub_top)
    new_ranks = _top_hubs(current, hub_top)
    old_scores = {node.id: node.pagerank for node in baseline.nodes.values()}
    new_scores = {node.id: node.pagerank for node in current.nodes.values()}

    shifts: list[HubShift] = []
    for node_id in sorted(set(old_ranks) | set(new_ranks)):
        old_rank = old_ranks.get(node_id)
        new_rank = new_ranks.get(node_id)
        old_score = old_scores.get(node_id, 0.0)
        new_score = new_scores.get(node_id, 0.0)
        left = old_rank is not None and new_rank is None
        entered = old_rank is None and new_rank is not None
        collapsed = (
            old_rank is not None
            and new_rank is not None
            and old_score > 0.0
            and new_score < old_score * (1.0 - hub_score_drop)
        )
        if left or entered or collapsed:
            shifts.append(
                HubShift(
                    node_id=node_id,
                    old_rank=old_rank,
                    new_rank=new_rank,
                    old_score=old_score,
                    new_score=new_score,
                )
            )

    return GraphDiffReport(
        baseline_nodes=len(baseline.nodes),
        current_nodes=len(current.nodes),
        new_dead=new_dead,
        resolved_dead=resolved_dead,
        hub_shifts=shifts,
    )


def render_diff_markdown(report: GraphDiffReport, *, project: str = "") -> str:
    """Render the report as the PR-comment markdown body."""
    title = f"## 🏗️ 架构体检报告{' · ' + project if project else ''}"
    lines = [
        title,
        "",
        f"基线 {report.baseline_nodes} 节点 → 当前 {report.current_nodes} 节点。"
        f"共 **{report.findings_count}** 项需关注。",
        "",
    ]
    if report.new_dead:
        lines += [f"### 🚨 新增死代码（{len(report.new_dead)}）", ""]
        for node_id in report.new_dead:
            lines.append(f"- `{node_id}`")
        lines.append("")
    if report.hub_shifts:
        lines += [
            "### ⚠️ 核心枢纽变化",
            "",
            "| 符号 | 基线排名 → 当前排名 | 分数变化 |",
            "|---|---|---|",
        ]
        for shift in report.hub_shifts:
            rank_text = f"{shift.old_rank or '—'} → {shift.new_rank or '出局'}"
            if shift.old_rank is None:
                rank_text = f"— → {shift.new_rank}"
            lines.append(
                f"| `{shift.node_id}` | {rank_text} | "
                f"{shift.old_score:.4f} → {shift.new_score:.4f} |"
            )
        lines.append("")
    if report.resolved_dead:
        shown = ", ".join(f"`{node_id}`" for node_id in report.resolved_dead[:10])
        lines += [f"### ✅ 已复活/删除的死代码候选：{shown}", ""]
    if report.findings_count == 0 and not report.resolved_dead:
        lines += ["未发现新增死代码或核心枢纽剧变。图结构保持稳定。✅", ""]
    lines += [
        "---",
        "",
        "<sub>由 [moray-scribe](https://github.com/aliangmo2026/moray-scribe) "
        "架构体检自动生成：静态分析确定性结果，非 AI 推断。</sub>",
    ]
    return "\n".join(lines)
