"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from moray_scribe.describer import OllamaClient, PromptContext, build_prompt_context
from moray_scribe.grapher import build_symbol_graph
from moray_scribe.scanner import extract_file
from moray_scribe.schema import ArchitectureDraft, RawSymbolBundle, SymbolGraph

# Modules extracted with root=sample_project so ids stay short (core.Engine).
_SAMPLE_MODULES = (
    "async_ops.py",
    "calls.py",
    "core.py",
    "decorators.py",
    "imports_mess.py",
    "inheritance.py",
    "nested.py",
    "params.py",
    "service.py",
)


class _FakeOllamaBackend:
    """A stand-in for ``ollama.Client`` that records calls and replays responses.

    Each entry in ``responses`` is either a string (returned as the message
    content) or an Exception (raised to simulate a transport failure).
    """

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.call_log: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.call_log.append(kwargs)
        if not self.responses:
            raise StopIteration("no more fake responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"message": {"content": item}}


@pytest.fixture()
def sample_project_path() -> Path:
    """Absolute path to the checked-in sample legacy project."""
    root = Path(__file__).resolve().parent / "fixtures" / "sample_project"
    assert root.is_dir(), f"missing fixture project: {root}"
    return root


@pytest.fixture()
def sample_bundles(sample_project_path: Path) -> list[RawSymbolBundle]:
    """Scanner bundles for every sample module (the package __init__ is skipped)."""
    return [
        extract_file(sample_project_path / name, root=sample_project_path)
        for name in _SAMPLE_MODULES
    ]


@pytest.fixture()
def sample_graph(sample_bundles: list[RawSymbolBundle]) -> SymbolGraph:
    """A conservatively built symbol graph over the whole sample project."""
    return build_symbol_graph(sample_bundles)


@pytest.fixture()
def fake_ollama_client() -> Any:
    """Factory returning an OllamaClient backed by a controllable fake.

    Usage: ``client = fake_ollama_client([valid_json, ConnectionError("x")])``.
    Inspect recorded calls via ``client.backend.call_log``.
    """

    def _make(responses: list[Any]) -> OllamaClient:
        client = OllamaClient(host="http://localhost:11434")
        backend = _FakeOllamaBackend(responses)
        client._client = backend  # type: ignore[attr-defined]
        client.backend = backend  # type: ignore[attr-defined]
        return client

    return _make


@pytest.fixture()
def sample_prompt_context(sample_graph: SymbolGraph) -> PromptContext:
    """A prompt context built from the sample project graph."""
    return build_prompt_context(
        sample_graph,
        project_name="sample",
        root_path="tests/fixtures/sample_project",
    )


@pytest.fixture()
def sample_rendered_graph(sample_graph: SymbolGraph) -> SymbolGraph:
    """Full scan->build graph for the sample project (same as sample_graph)."""
    return sample_graph


@pytest.fixture()
def sample_draft() -> ArchitectureDraft:
    """A structurally complete draft covering flowchart + classDiagram + layers."""
    return ArchitectureDraft.model_validate(
        {
            "title": "Sample System",
            "summary": (
                "A sample architecture covering presentation, domain and data layers "
                "with a clear separation of concerns across the codebase."
            ),
            "diagrams": [
                {
                    "kind": "flowchart",
                    "direction": "TD",
                    "title": "Architecture",
                    "nodes": [
                        {
                            "id": "Presentation",
                            "label": 'Presentation "Layer"',
                            "kind": "service",
                            "group": "Presentation",
                        },
                        {
                            "id": "Domain",
                            "label": "Domain [Core]",
                            "kind": "module",
                            "group": "Domain",
                        },
                        {"id": "Store", "label": "Store", "kind": "database"},
                        {"id": "SMTP", "label": "External: SMTP", "kind": "external"},
                        {"id": "Main", "label": "Main", "kind": "entrypoint"},
                    ],
                    "edges": [
                        {
                            "source": "Presentation",
                            "target": "Domain",
                            "label": "calls",
                            "kind": "calls",
                            "style": "solid",
                        },
                        {
                            "source": "Domain",
                            "target": "Store",
                            "kind": "depends_on",
                            "style": "dashed",
                        },
                        {
                            "source": "Domain",
                            "target": "SMTP",
                            "kind": "depends_on",
                            "style": "thick",
                        },
                    ],
                },
                {
                    "kind": "classDiagram",
                    "title": "Details",
                    "nodes": [
                        {"id": "Domain", "label": "Domain", "kind": "module"},
                        {"id": "Repo", "label": "Repo", "kind": "class"},
                    ],
                    "edges": [{"source": "Repo", "target": "Domain", "kind": "inherits"}],
                },
            ],
            "modules": [
                {
                    "module": "core",
                    "summary": "Core domain.",
                    "responsibilities": ["Engine orchestration"],
                    "symbol_ids": ["core.Engine", "core.build_engine"],
                    "risk": "medium",
                },
                {
                    "module": "service",
                    "summary": "Service layer.",
                    "symbol_ids": ["service.Service"],
                    "risk": "low",
                },
            ],
            "core_hubs": ["core.Engine"],
            "dead_code": ["service.Service"],
            "risks": ["Tight coupling to the store."],
            "recommendations": ["Extract a repository interface."],
        }
    )
