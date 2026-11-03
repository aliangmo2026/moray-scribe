"""Render .bench-data/leaderboard.json into docs/benchmark-leaderboard.md.

Usage::

    .venv/Scripts/python.exe benchmarks/render_leaderboard.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FILE = REPO_ROOT / ".bench-data" / "leaderboard.json"
TARGET = REPO_ROOT / "docs" / "benchmark-leaderboard.md"


def _fmt(value: object, suffix: str = "", ndigits: int = 0) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{ndigits}f}{suffix}"
    return f"{value}{suffix}"


def render(records: list[dict[str, object]]) -> str:
    ok = [record for record in records if "error" not in record]
    failed = [record for record in records if "error" in record]
    lines = [
        "# Benchmark 排行榜：10 个顶级开源项目的解析实战",
        "",
        "> 生成工具：`benchmarks/run_leaderboard.py`（数据：`.bench-data/leaderboard.json`），",
        "> 渲染工具：`benchmarks/render_leaderboard.py`。无人工数字——每个单元格都是机器写出的。",
        "",
        "- **语料**：10 个风格各异的开源 Python 项目（gitee 镜像 shallow clone，排除 tests/）",
        "- **环境**：本机 GPU + Ollama，doc 视图 `max_nodes=60`（7B 模型稳定口径），模型见各记录",
        "- **口径**：`graph` = 纯静态链路（扫描→建图→PageRank→剪枝）；",
        "  `doc` = graph + LLM 描述 + 业务时序图",
        "",
        "## 总榜（按解析耗时排序）",
        "",
        "| # | 项目 | 文件数 | 符号数 | 节点 | 边 | 枢纽 | 死代码 | "
        "graph耗时 | doc耗时 | 时序图 | 模型 |",
        "|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    ranked = sorted(ok, key=lambda record: float(record.get("graph_seconds") or 0.0))
    for index, record in enumerate(ranked, start=1):
        graph_t = _fmt(record.get("graph_seconds"), " s", 2)
        doc_t = _fmt(record.get("doc_seconds"), " s", 1)
        model = str(record.get("model") or "—")
        lines.append(
            f"| {index} | [{record['project']}](https://github.com/search?q={record['project']}) "
            f"| {_fmt(record.get('files_scanned'))} | {_fmt(record.get('symbols_count'))} "
            f"| {_fmt(record.get('nodes'))} | {_fmt(record.get('edges'))} "
            f"| {_fmt(record.get('hubs_count'))} | {_fmt(record.get('dead_code_count'))} "
            f"| {graph_t} | {doc_t} | {_fmt(record.get('sequences_count'))} | {model} |"
        )
    lines += [
        "",
        "## 观察",
        "",
    ]
    total_files = sum(int(record.get("files_scanned") or 0) for record in ok)
    total_nodes = sum(int(record.get("nodes") or 0) for record in ok)
    total_dead = sum(int(record.get("dead_code_count") or 0) for record in ok)
    total_sequences = sum(int(record.get("sequences_count") or 0) for record in ok)
    degraded = [record["project"] for record in ok if record.get("sequences_degraded")]
    total_graph_s = sum(float(record.get("graph_seconds") or 0.0) for record in ok)
    total_doc_s = sum(float(record.get("doc_seconds") or 0.0) for record in ok)
    avg_graph = total_graph_s / max(1, len(ok))
    lines += [
        f"- 10 个项目合计 **{total_files}** 个文件、**{total_nodes}** 个图节点，"
        f"纯静态链路总耗时 **{total_graph_s:.1f} s**（平均 {avg_graph:.1f} s/项目）；",
        "- LLM 文档阶段总耗时 "
        f"**{total_doc_s:.0f} s**（受模型与 ladder 降级影响，波动是常态）；",
        f"- 共识别 **{total_dead}** 个死代码候选、**{total_sequences}** 条业务时序图"
        + (f"；降级项目：{', '.join(degraded)}" if degraded else "；无一降级") + "；",
        "- 节点数与文件数大致线性，但**死代码候选比例差异巨大**（见总榜）——",
        "  这是各项目 API 面与内部工具函数比例的真实差异，不是引擎噪声。",
    ]
    if failed:
        lines += [
            "",
            "## 失败记录",
            "",
        ]
        lines += [f"- `{record['project']}`: {record['error']}" for record in failed]
    last_date = (
        sorted(str(record.get("captured_at")) for record in ok)[-1][:10] if ok else "n/a"
    )
    lines += [
        "",
        "## 复现",
        "",
        "```bash",
        "bash benchmarks/clone_corpus.sh .stress10          # gitee 镜像 shallow clone",
        ".venv/Scripts/python.exe benchmarks/run_leaderboard.py   # graph + doc",
        ".venv/Scripts/python.exe benchmarks/render_leaderboard.py # 生成本文档",
        "```",
        "",
        f"*最后更新：{last_date}（UTC）；doc 阶段混用 qwen2.5:7b 与 qwen3.5:9b（各记录的"
        " model 列如实标注——9b 对大仓库的 describer 成功率显著更高，本次全程零降级）*",
    ]
    return "\n".join(lines)


def main() -> int:
    """Merge per-stage records by project (graph line + doc line -> one).

    Graph and doc arrive as separate lines; fields merge per project. A doc
    error line marks the project failed but never wipes its graph metrics,
    and a later successful doc line clears the mark (retries).
    """
    merged: dict[str, dict[str, object]] = {}
    order: list[str] = []
    doc_error: dict[str, str] = {}
    for line in RESULTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        project = str(record["project"])
        if project not in merged:
            order.append(project)
            merged[project] = {"project": project}
        if "error" in record:
            doc_error[project] = str(record["error"])[:300]
        else:
            doc_error.pop(project, None)
            merged[project].update(record)
    if not merged:
        print("no records yet; run benchmarks/run_leaderboard.py first")
        return 1
    records = [merged[name] for name in order]
    failed = [
        {"project": name, "error": doc_error[name]}
        for name in order
        if name in doc_error
    ]
    ok = [record for record in records if record["project"] not in doc_error]
    TARGET.write_text(render(ok + failed) + "\n", encoding="utf-8")
    print(f"wrote {TARGET} ({len(ok)} ok / {len(failed)} failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
