"""Typer command-line interface for moray-scribe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from moray_scribe import __version__
from moray_scribe.config import Settings, load_settings
from moray_scribe.exceptions import ScribeError
from moray_scribe.grapher.diff import diff_graphs, render_diff_markdown
from moray_scribe.logging import setup_logging
from moray_scribe.mcp.server import run_stdio_server
from moray_scribe.pipeline import Mode, run, run_with_result
from moray_scribe.schema import DiagramKind, Direction, SymbolGraph

app = typer.Typer(
    name="moray-scribe",
    help="Reverse documentation and architecture diagrams for legacy Python code.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    """Eagerly print the version and exit when ``--version`` is supplied."""
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_print_version,
            is_eager=True,
            help="Print the moray-scribe version and exit.",
        ),
    ] = False,
) -> None:
    """Reverse-engineer documentation and diagrams from a legacy codebase."""


def _resolve_settings(config: Path | None, verbose: bool) -> Settings:
    """Apply logging, then load settings with an optional config file."""
    setup_logging("DEBUG" if verbose else "INFO")
    return load_settings(config)


def _execute(mode: Mode, path: Path, cfg: Settings, out: Path | None = None) -> None:
    """Run one pipeline mode and print the artifact path."""
    try:
        artifact = run(mode, path, cfg, out_dir=out)
    except NotImplementedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(str(artifact))


@app.command()
def scan(
    path: Annotated[Path, typer.Argument(help="Project directory or Python file to scan.")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Destination for the symbol inventory."),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", metavar="PATTERN", help="Include glob; repeatable."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", metavar="PATTERN", help="Exclude glob; repeatable."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Scan a codebase and export its Python symbols."""
    cfg = _resolve_settings(config, verbose)
    updates: dict[str, object] = {}
    if include is not None:
        updates["include"] = list(include)
    if exclude is not None:
        updates["exclude"] = list(exclude)
    if updates:
        cfg = cfg.model_copy(update=updates)
    _execute("scan", path, cfg, out)


@app.command()
def graph(
    path: Annotated[Path, typer.Argument(help="Project directory or Python file to scan.")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Destination for the serialized symbol graph."),
    ] = None,
    max_nodes: Annotated[
        int,
        typer.Option("--max-nodes", min=1, help="Maximum number of nodes to keep."),
    ] = 200,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", metavar="PATTERN", help="Include glob; repeatable."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", metavar="PATTERN", help="Exclude glob; repeatable."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Build and analyze the symbol graph for a codebase."""
    cfg = _resolve_settings(config, verbose)
    updates: dict[str, object] = {"max_nodes": max_nodes}
    if include is not None:
        updates["include"] = list(include)
    if exclude is not None:
        updates["exclude"] = list(exclude)
    cfg = cfg.model_copy(update=updates)
    _execute("graph", path, cfg, out)


@app.command()
def diagram(
    path: Annotated[Path, typer.Argument(help="Project directory or Python file to scan.")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Destination for the Mermaid diagram."),
    ] = None,
    diagram_kind: Annotated[
        DiagramKind,
        typer.Option("--diagram-kind", help="Mermaid diagram family."),
    ] = DiagramKind.FLOWCHART,
    direction: Annotated[
        Direction,
        typer.Option("--direction", help="Mermaid layout direction."),
    ] = Direction.TD,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", min=60, help="Ollama request timeout in seconds."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Generate a Mermaid architecture diagram."""
    cfg = _resolve_settings(config, verbose)
    if timeout is not None:
        cfg = cfg.model_copy(update={"request_timeout_s": timeout})
    _execute("diagram", path, cfg, out)


@app.command()
def doc(
    path: Annotated[Path, typer.Argument(help="Project directory or Python file to scan.")],
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Directory for the generated Markdown document."),
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", min=60, help="Ollama request timeout in seconds."),
    ] = None,
    infer: Annotated[
        bool | None,
        typer.Option(
            "--infer/--no-infer",
            help="Run LLM dynamic inference on dangling calls (default: settings).",
        ),
    ] = None,
    infer_calls: Annotated[
        int | None,
        typer.Option(
            "--infer-max-calls",
            min=0,
            help="Cap on dangling calls sent to the model (top PageRank first).",
        ),
    ] = None,
    infer_concurrency: Annotated[
        int | None,
        typer.Option(
            "--infer-concurrency",
            min=1,
            max=16,
            help=(
                "Concurrent inference requests; tune to GPU VRAM "
                "(see docs/ultimate-benchmark.md)."
            ),
        ),
    ] = None,
    infer_batch: Annotated[
        int | None,
        typer.Option(
            "--infer-batch-size",
            min=1,
            max=64,
            help="Call sites per inference request.",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Generate the full Markdown reverse-engineering document."""
    cfg = _resolve_settings(config, verbose)
    updates: dict[str, object] = {}
    if timeout is not None:
        updates["request_timeout_s"] = timeout
    if infer is not None:
        updates["inference_enabled"] = infer
    if infer_calls is not None:
        updates["inference_max_calls"] = infer_calls
    if infer_concurrency is not None:
        updates["inference_concurrency"] = infer_concurrency
    if infer_batch is not None:
        updates["inference_batch_size"] = infer_batch
    if updates:
        cfg = cfg.model_copy(update=updates)
    _execute("doc", path, cfg, out)


def _load_graph_file(path: Path, label: str) -> SymbolGraph:
    """Load a pruned-graph.json, raising :class:`ScribeError` with context."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScribeError(f"cannot read {label} at {path}: {exc}") from exc
    try:
        return SymbolGraph.model_validate_json(text)
    except ValidationError as exc:
        raise ScribeError(f"{label} at {path} is not a valid pruned-graph.json: {exc}") from exc


@app.command()
def check(
    path: Annotated[
        Path,
        typer.Argument(help="Project directory to analyze (used when --current is absent)."),
    ],
    baseline: Annotated[
        Path,
        typer.Option("--baseline", help="Baseline pruned-graph.json (e.g. the PR merge-base)."),
    ],
    current: Annotated[
        Path | None,
        typer.Option(
            "--current",
            help="Pre-built current pruned-graph.json (default: build from PATH).",
        ),
    ] = None,
    hub_top: Annotated[
        int,
        typer.Option("--hub-top", min=1, help="Size of the PageRank hub watchlist."),
    ] = 10,
    hub_score_drop: Annotated[
        float,
        typer.Option(
            "--hub-score-drop",
            min=0.0,
            max=1.0,
            help="Relative score collapse (0.5 = -50%) that counts as a hub shift.",
        ),
    ] = 0.5,
    fail_on: Annotated[
        str,
        typer.Option(
            "--fail-on",
            help="Which findings exit non-zero: dead | hub | any | none.",
        ),
    ] = "dead",
    max_nodes: Annotated[
        int,
        typer.Option("--max-nodes", min=1, help="Maximum number of nodes to keep."),
    ] = 200,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", metavar="PATTERN", help="Include glob; repeatable."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", metavar="PATTERN", help="Exclude glob; repeatable."),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Write the PR-comment markdown report here."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="Destination for the freshly built current graph."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Architecture check: diff a project against a baseline graph.

    Designed for CI: non-zero exit when the PR adds dead code (--fail-on dead,
    the default) or reshuffles core hubs (--fail-on hub|any); --report emits
    the markdown body for a PR comment. See docs/github_action.md.
    """
    cfg = _resolve_settings(config, verbose)
    updates: dict[str, object] = {"max_nodes": max_nodes}
    if include is not None:
        updates["include"] = list(include)
    if exclude is not None:
        updates["exclude"] = list(exclude)
    cfg = cfg.model_copy(update=updates)
    triggers = {"dead": ("new_dead",), "hub": ("hub_shifts",), "any": ("new_dead", "hub_shifts")}
    if fail_on not in triggers and fail_on != "none":
        typer.echo(f"error: --fail-on must be dead|hub|any|none, got {fail_on!r}", err=True)
        raise typer.Exit(code=2)
    try:
        baseline_graph = _load_graph_file(baseline, "baseline graph")
        if current is not None:
            current_graph = _load_graph_file(current, "current graph")
        else:
            result = run_with_result("graph", path, cfg, out_dir=out)
            built = result.out_path
            if built is None:  # pragma: no cover - graph mode always sets out_path
                raise ScribeError("graph run produced no artifact")
            current_graph = _load_graph_file(built, "current graph")
    except ScribeError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    diff = diff_graphs(
        baseline_graph,
        current_graph,
        hub_top=hub_top,
        hub_score_drop=hub_score_drop,
    )
    typer.echo(f"🏗️ 架构体检: {diff.baseline_nodes} → {diff.current_nodes} 节点")
    typer.echo(f"   新增死代码: {len(diff.new_dead)}")
    for node_id in diff.new_dead:
        typer.echo(f"     - {node_id}")
    typer.echo(f"   枢纽变化: {len(diff.hub_shifts)}")
    for shift in diff.hub_shifts:
        typer.echo(
            f"     - {shift.node_id} (rank {shift.old_rank or '—'}→{shift.new_rank or '出局'}, "
            f"score {shift.old_score:.4f}→{shift.new_score:.4f})"
        )
    if diff.resolved_dead:
        typer.echo(f"   已复活/删除的死代码候选: {len(diff.resolved_dead)}")

    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            render_diff_markdown(diff, project=path.name), encoding="utf-8"
        )
        typer.echo(f"   报告: {report}")

    triggered = fail_on != "none" and any(
        getattr(diff, field_name) for field_name in triggers[fail_on]
    )
    typer.echo(f"   结论: {'ALERT' if triggered else 'PASS'}")
    if triggered:
        raise typer.Exit(code=1)


@app.command()
def serve_mcp(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a .env configuration file."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Serve moray-scribe capabilities over the MCP stdio transport."""
    _resolve_settings(config, verbose)
    try:
        asyncio.run(run_stdio_server())
    except NotImplementedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
