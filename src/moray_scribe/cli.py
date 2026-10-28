"""Typer command-line interface for moray-scribe."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from moray_scribe import __version__
from moray_scribe.config import Settings, load_settings
from moray_scribe.logging import setup_logging
from moray_scribe.mcp.server import run_stdio_server
from moray_scribe.pipeline import Mode, run
from moray_scribe.schema import DiagramKind, Direction

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
    cfg = _resolve_settings(config, verbose).model_copy(update={"max_nodes": max_nodes})
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
    if timeout is not None:
        cfg = cfg.model_copy(update={"request_timeout_s": timeout})
    _execute("doc", path, cfg, out)


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
