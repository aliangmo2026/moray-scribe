"""Generate SHOWCASE.md: deterministic architecture views of three OSS projects.

For each project the script

1. scans and builds the full symbol graph (no LLM),
2. aggregates it into a module-level Mermaid flowchart (top modules by
   PageRank, edge labels = aggregated relation counts),
3. collects hard facts (hubs, routes, ORM models, data flows),
4. asks the local Ollama model for ONE Chinese sentence of commentary,
5. writes ``SHOWCASE.md`` at the repository root.

Diagrams are deterministic engine output — the AI only writes the sentence,
so nothing in the pictures can be hallucinated.

Usage::

    python benchmarks/showcase.py --projects flask,sqlalchemy,django
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import ollama
from loguru import logger

from moray_scribe import __version__
from moray_scribe.config import Settings
from moray_scribe.grapher import (
    annotate_degrees,
    build_symbol_graph,
    compute_pagerank,
    detect_dead_code,
)
from moray_scribe.pipeline import resolve_scan_root
from moray_scribe.scanner import extract_file, iter_python_files
from moray_scribe.schema import EdgeKind, SymbolGraph

REPO_ROOT = Path(__file__).resolve().parent.parent
STRESS_DIR = REPO_ROOT / ".stress"

PROJECTS = {
    "flask": {
        "dir": "flask/src/flask",
        "title": "pallets/flask",
        "group": "Web 框架",
        "exclude": ["**/test_*.py", "**/tests/**", "**/__pycache__/**"],
    },
    "sqlalchemy": {
        "dir": "sqlalchemy/lib/sqlalchemy",
        "title": "sqlalchemy/sqlalchemy",
        "group": "ORM / 数据库工具包",
        # `testing/` is SQLAlchemy's own test harness, not part of the library.
        "exclude": ["**/test_*.py", "**/tests/**", "**/testing/**", "**/__pycache__/**"],
    },
    "django": {
        "dir": "django/django",
        "title": "django/django",
        "group": "全栈 Web 框架",
        # Django keeps its suite in `tests/` with files named `tests.py`, which
        # the default `test_*.py` exclusion does not catch.
        "exclude": ["**/tests/**", "**/test_*.py", "**/docs/**", "**/__pycache__/**"],
    },
}

_COMMENTARY_PROMPT = """\
You are reviewing a reverse-engineered architecture summary of an open-source \
Python project. Based ONLY on the facts below, write ONE Chinese sentence \
(at most 45 characters) about how the code is ORGANIZED.

Hard rules:
- Start with one concrete subsystem name taken from "Top modules".
- Then state its architectural ROLE (hub / layering / entry / shared foundation).
- Do NOT use generic praise words: 复杂, 强大, 丰富, 优秀, 简洁, 核心.
- Do NOT restate file/symbol counts.
- At most 45 Chinese characters. No preamble, no quotes, no markdown.

Good examples:
- "compiler 是中枢，把方言差异收敛到 dialect 层"
- "app 作为入口层，向下依赖 cli 与 helpers"

Project: {title} ({group})
Top modules: {top_modules}
Top hubs: {hubs}
HTTP routes detected: {routes}
ORM model classes: {orm}
Confirmed ORM data flows: {flows}
"""


@dataclass
class ProjectFacts:
    key: str
    title: str
    group: str
    files: int
    symbols: int
    nodes: int
    edges: int
    modules: int
    routes: int
    orm: int
    flows: int
    dead: int
    hubs: list[str]
    top_modules: list[tuple[str, int]]
    diagram: str


def _scan(path: Path, cfg: Settings, exclude: list[str]) -> list:
    root = resolve_scan_root(path)
    scoped = cfg.model_copy(update={"exclude": [*cfg.exclude, *exclude]})
    return [
        extract_file(p, root=root)
        for p in iter_python_files(
            root,
            include=scoped.include,
            exclude=scoped.exclude,
            max_size_kb=scoped.max_file_size_kb,
        )
    ]


def _safe_id(text: str) -> str:
    cleaned = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch == "_")) else "_" for ch in text
    ).strip("_")
    return cleaned or "module"


def _module_diagram(
    graph: SymbolGraph, *, max_modules: int = 12
) -> tuple[str, list[tuple[str, int]]]:
    """Aggregate the symbol graph into a module-level Mermaid flowchart."""
    scores: dict[str, float] = {}
    sizes: dict[str, int] = {}
    orm_modules: set[str] = set()
    for node in graph.nodes.values():
        module = node.module
        if not module:
            continue
        scores[module] = scores.get(module, 0.0) + node.pagerank
        sizes[module] = sizes.get(module, 0) + 1
        if "orm_model" in node.tags:
            orm_modules.add(module)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    kept = [module for module, _ in ranked[:max_modules]]
    kept_set = set(kept)
    top_modules = [(module, sizes.get(module, 0)) for module in kept]

    relation_counts: dict[tuple[str, str], int] = {}
    for edge in graph.edges:
        if edge.kind not in (EdgeKind.IMPORTS, EdgeKind.CALLS, EdgeKind.INHERITS):
            continue
        src = graph.nodes.get(edge.source)
        dst = graph.nodes.get(edge.target)
        if src is None or dst is None:
            continue
        if src.module not in kept_set or dst.module not in kept_set:
            continue
        if src.module == dst.module:
            continue
        relation_counts[(src.module, dst.module)] = (
            relation_counts.get((src.module, dst.module), 0) + 1
        )

    id_by_module = {module: _safe_id(module) for module in kept}
    lines = ["flowchart LR"]
    for module in kept:
        label = f"{module.split('.')[-1]}<br/>{sizes.get(module, 0)} syms"
        shape = f'[("{label}")]' if module in orm_modules else f'["{label}"]'
        lines.append(f"    {id_by_module[module]}{shape}")

    top_relations = sorted(relation_counts.items(), key=lambda item: -item[1])[:18]
    for (src, dst), count in top_relations:
        lines.append(f'    {id_by_module[src]} -->|"{count}"| {id_by_module[dst]}')

    orm_ids = [id_by_module[m] for m in kept if m in orm_modules]
    if orm_ids:
        lines.append("    classDef database fill:#fff3e0,stroke:#f57c00")
        lines.append(f"    class {','.join(orm_ids)} database")
    return "\n".join(lines), top_modules


def _commentary(facts: ProjectFacts, cfg: Settings) -> str:
    """Ask the local model for one sentence; fall back to a factual line."""
    prompt = _COMMENTARY_PROMPT.format(
        title=facts.title,
        group=facts.group,
        hubs=", ".join(facts.hubs[:3]) or "(none)",
        top_modules=", ".join(
            f"{name.rsplit('.', 1)[-1]} ({size} syms)" for name, size in facts.top_modules[:5]
        ),
        routes=facts.routes,
        orm=facts.orm,
        flows=facts.flows,
    )
    try:
        client = ollama.Client(host=cfg.ollama_host, timeout=120)
        response = client.chat(
            model=cfg.model_summarizer,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 80, "num_ctx": 4096},
        )
        text = (response.get("message", {}).get("content") or "").strip()
        text = text.strip("`\"'“” \n")
        # Reject answers that ignore the required shape: they must name one of
        # the top modules, otherwise the sentence is generic filler.
        short_names = [name.rsplit(".", 1)[-1] for name, _ in facts.top_modules[:5]]
        if 0 < len(text) <= 120 and any(name in text for name in short_names):
            return text
    except Exception as exc:
        logger.warning("commentary model unavailable: {!r}", exc)
    pivot = facts.top_modules[0][0] if facts.top_modules else "核心模块"
    return f"{facts.title} 以 {pivot} 为枢纽组织代码。"


def analyse(key: str, cfg: Settings) -> ProjectFacts:
    spec = PROJECTS[key]
    path = STRESS_DIR / spec["dir"]
    if not path.is_dir():
        raise FileNotFoundError(f"missing project checkout: {path}")
    bundles = _scan(path, cfg, spec.get("exclude", []))
    graph = build_symbol_graph(bundles)
    annotate_degrees(graph)
    compute_pagerank(graph)
    dead = detect_dead_code(graph)
    del bundles

    routes = sum(1 for node in graph.nodes.values() if node.route is not None)
    orm = sum(1 for node in graph.nodes.values() if "orm_model" in node.tags)
    flows = sum(1 for edge in graph.edges if edge.kind is EdgeKind.DATA_FLOW)
    diagram, top_modules = _module_diagram(graph)
    hubs = [
        node_id
        for node_id, _ in sorted(
            ((nid, node.pagerank) for nid, node in graph.nodes.items()),
            key=lambda item: -item[1],
        )[:5]
    ]
    return ProjectFacts(
        key=key,
        title=spec["title"],
        group=spec["group"],
        files=len({node.file for node in graph.nodes.values()}),
        symbols=sum(
            1 for node in graph.nodes.values() if node.kind.value != "module"
        ),
        nodes=graph.node_count,
        edges=graph.edge_count,
        modules=sum(1 for node in graph.nodes.values() if node.kind.value == "module"),
        routes=routes,
        orm=orm,
        flows=flows,
        dead=len(dead),
        hubs=hubs,
        top_modules=[(name, size) for name, size in top_modules],
        diagram=diagram,
    )


def render(facts: list[ProjectFacts], commentaries: dict[str, str]) -> str:
    lines = [
        "# moray-scribe SHOWCASE",
        "",
        f"> 由 moray-scribe v{__version__} 自动生成 · 图谱为**确定性静态分析**产物，",
        "> 仅每节末尾的一句点评由本地大模型撰写。",
        "",
        "三个顶级开源项目的逆向架构视图：每个项目只扫描、建图、聚合模块，",
        "**不让模型参与画图**——图里的每条边都能回溯到源码。",
        "",
        "| 项目 | 领域 | Python 文件 | 符号 | 图节点/边 | 路由 | ORM 模型 | 数据流 |",
        "|---|---|---:|---:|---|---:|---:|---:|",
    ]
    for fact in facts:
        lines.append(
            f"| [{fact.title}](#{fact.key}) | {fact.group} | {fact.files:,} | "
            f"{fact.symbols:,} | {fact.nodes:,}/{fact.edges:,} | {fact.routes} | "
            f"{fact.orm} | {fact.flows} |"
        )
    lines.append("")

    for fact in facts:
        lines += [
            f'<a id="{fact.key}"></a>',
            "",
            f"## {fact.title}",
            "",
            f"**规模**：{fact.files:,} 个 Python 文件 · {fact.symbols:,} 个符号 · "
            f"{fact.modules:,} 个模块 · {fact.dead:,} 个死代码候选",
            "",
            "**模块级核心架构**（按 PageRank 取前 12 个模块；箭头上的数字是聚合后的",
            "导入/调用/继承关系数；虚线框圆角节点 = 含 ORM 模型的模块）：",
            "",
            "```mermaid",
            fact.diagram,
            "```",
            "",
            "**引擎观测**",
            "",
            f"- 核心枢纽：{', '.join(f'`{hub}`' for hub in fact.hubs[:3])}",
            f"- 静态路由：{fact.routes} 条 · ORM 模型类：{fact.orm} 个 · "
            f"确认数据流：{fact.flows} 条",
            "",
            f"> 💬 **AI 点评**：{commentaries[fact.key]}",
            "",
            "---",
            "",
        ]

    lines += [
        "## 复现方式",
        "",
        "```bash",
        "# 1) 只做确定性拓扑（无需模型，秒级）",
        "moray-scribe graph path/to/project --out out/",
        "",
        "# 2) 交互式查看同一张图",
        "python web_viewer/serve.py out/pruned-graph.json",
        "",
        "# 3) 重新生成本文件（需要 .stress/ 下的三个克隆）",
        "python benchmarks/showcase.py --projects flask,sqlalchemy,django",
        "```",
        "",
        f"*生成工具：moray-scribe v{__version__} · 数据源：本地浅克隆的官方仓库*",
    ]
    return "\n".join(lines) + "\n"


def _load_facts(path: Path) -> list[ProjectFacts]:
    """Reload facts saved by a previous run (used by ``--reuse``)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    facts: list[ProjectFacts] = []
    for item in raw:
        item = dict(item)
        item.pop("commentary", None)
        item["top_modules"] = [tuple(pair) for pair in item["top_modules"]]
        facts.append(ProjectFacts(**item))
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SHOWCASE.md.")
    parser.add_argument("--projects", default="flask,sqlalchemy,django")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "SHOWCASE.md")
    parser.add_argument("--model", default=None, help="Model for the one-line commentary.")
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse .stress/showcase-facts.json (skip scanning) and only redo the commentary.",
    )
    args = parser.parse_args(argv)

    cfg = Settings()
    if args.model:
        cfg = cfg.model_copy(update={"model_summarizer": args.model})

    keys = [item.strip() for item in args.projects.split(",") if item.strip()]
    facts_path = REPO_ROOT / ".stress" / "showcase-facts.json"
    if args.reuse:
        if not facts_path.is_file():
            parser.error(f"--reuse needs {facts_path}")
        cached = {fact.key: fact for fact in _load_facts(facts_path)}
        missing = [key for key in keys if key not in cached]
        if missing:
            parser.error(f"cached facts missing projects: {missing}")
        facts = [cached[key] for key in keys]
    else:
        facts = []
        for key in keys:
            if key not in PROJECTS:
                parser.error(f"unknown project {key!r}; choose from {sorted(PROJECTS)}")
            print(f"analysing {key} …", flush=True)
            fact = analyse(key, cfg)
            facts.append(fact)
            print(
                f"  {fact.files} files, {fact.symbols} symbols, {fact.routes} routes, "
                f"{fact.orm} ORM models, {fact.flows} data flows",
                flush=True,
            )

    commentaries: dict[str, str] = {}
    for fact in facts:
        commentaries[fact.key] = _commentary(fact, cfg)
        print(f"  commentary[{fact.key}]: {commentaries[fact.key]}", flush=True)

    args.out.write_text(render(facts, commentaries), encoding="utf-8")
    print(f"written: {args.out}")
    facts_path.write_text(
        json.dumps(
            [{**fact.__dict__, "commentary": commentaries[fact.key]} for fact in facts],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
