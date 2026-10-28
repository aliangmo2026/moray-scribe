"""MCP stdio server exposing moray-scribe to agent hosts."""

from __future__ import annotations

from .server import app, run_stdio_server

__all__ = ["app", "run_stdio_server"]
