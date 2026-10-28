"""Contracts for the LLM-authored architecture draft and its Mermaid diagrams."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import StrictModel

# Mermaid node and subgraph ids are identifiers, never display text. Labels carry
# the human-readable name, so whitespace and punctuation are rejected here.
MERMAID_ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class DiagramKind(str, Enum):
    """Mermaid diagram family supported by the renderer."""

    FLOWCHART = "flowchart"
    CLASS_DIAGRAM = "classDiagram"


class Direction(str, Enum):
    """Supported Mermaid layout directions."""

    TD = "TD"
    LR = "LR"


class NodeKind(str, Enum):
    """Architectural role of a Mermaid node."""

    MODULE = "module"
    PACKAGE = "package"
    CLASS = "class"
    FUNCTION = "function"
    SERVICE = "service"
    INTERFACE = "interface"
    DATABASE = "database"
    EXTERNAL = "external"
    GATEWAY = "gateway"
    ENTRYPOINT = "entrypoint"
    UNKNOWN = "unknown"


class ArchEdgeKind(str, Enum):
    """Semantic meaning of an architecture-level relationship."""

    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"
    COMPOSES = "composes"
    DEPENDS_ON = "depends_on"
    USES = "uses"
    DATA_FLOW = "data_flow"


class EdgeStyle(str, Enum):
    """Mermaid line styles available to the renderer."""

    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    THICK = "thick"


class MermaidNode(StrictModel):
    """A node of a Mermaid diagram."""

    id: str = Field(
        pattern=MERMAID_ID_PATTERN,
        description="Mermaid identifier; must not contain whitespace.",
    )
    label: str = Field(min_length=1, description="Human-readable display label.")
    kind: NodeKind = Field(default=NodeKind.MODULE, description="Architectural role.")
    description: str = Field(default="", description="Optional node description.")
    module: str | None = Field(default=None, description="Owning dotted module path.")
    symbol_ids: list[str] = Field(
        default_factory=list,
        description="Symbols from the graph represented by this node.",
    )
    group: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        description=(
            "Logic layer name (may be Chinese). Rendered as the subgraph display "
            "name; an ASCII id is sanitized at render time."
        ),
    )
    critical: bool = Field(default=False, description="Whether the node is on a critical path.")

    @field_validator("group")
    @classmethod
    def _check_group_no_control_chars(cls, value: str | None) -> str | None:
        """Allow non-ASCII layer names but reject control characters."""
        if value is None:
            return value
        if any(ord(char) < 32 for char in value):
            raise ValueError("group must not contain control characters")
        return value


class MermaidEdge(StrictModel):
    """A directed relationship between two Mermaid nodes."""

    source: str = Field(pattern=MERMAID_ID_PATTERN, description="Source node id.")
    target: str = Field(pattern=MERMAID_ID_PATTERN, description="Target node id.")
    label: str = Field(default="", description="Optional edge label.")
    kind: ArchEdgeKind = Field(
        default=ArchEdgeKind.DEPENDS_ON,
        description="Semantic relationship kind.",
    )
    style: EdgeStyle = Field(default=EdgeStyle.SOLID, description="Rendered line style.")


class MermaidDiagram(StrictModel):
    """A validated Mermaid diagram whose edges reference only declared nodes."""

    kind: DiagramKind = Field(default=DiagramKind.FLOWCHART, description="Diagram family.")
    direction: Direction = Field(default=Direction.TD, description="Layout direction.")
    title: str = Field(default="", description="Diagram title.")
    description: str = Field(default="", description="Optional diagram description.")
    nodes: list[MermaidNode] = Field(default_factory=list, description="Declared nodes.")
    edges: list[MermaidEdge] = Field(default_factory=list, description="Declared edges.")

    @model_validator(mode="after")
    def _check_topology(self) -> MermaidDiagram:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                duplicates.add(node.id)
            seen.add(node.id)
        if duplicates:
            raise ValueError(f"duplicate node ids: {sorted(duplicates)}")

        missing: set[str] = set()
        for edge in self.edges:
            if edge.source not in seen:
                missing.add(edge.source)
            if edge.target not in seen:
                missing.add(edge.target)
        if missing:
            raise ValueError(f"edges reference undeclared node ids: {sorted(missing)}")
        return self

    @property
    def node_ids(self) -> set[str]:
        """All declared node ids."""
        return {node.id for node in self.nodes}

    def get_node(self, node_id: str) -> MermaidNode | None:
        """Return a node by id, or ``None`` when it is absent."""
        return next((node for node in self.nodes if node.id == node_id), None)


class ModuleNote(StrictModel):
    """Narrative documentation for one module in the draft."""

    module: str = Field(min_length=1, description="Dotted module path.")
    file: str = Field(default="", description="Repository-relative source path.")
    summary: str = Field(default="", description="One-paragraph module summary.")
    responsibilities: list[str] = Field(
        default_factory=list,
        description="Responsibilities owned by the module.",
    )
    symbol_ids: list[str] = Field(
        default_factory=list,
        description="Key symbols that illustrate the module's role.",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="Modules this module depends on.",
    )
    risk: Literal["low", "medium", "high"] = Field(
        default="low",
        description="Qualitative maintenance risk.",
    )


class ArchitectureDraft(StrictModel):
    """The complete, schema-validated output of the describer stage."""

    title: str = Field(min_length=1, description="Document title.")
    summary: str = Field(default="", description="Executive summary of the architecture.")
    diagrams: list[MermaidDiagram] = Field(
        default_factory=list,
        description="Architecture diagrams.",
    )
    modules: list[ModuleNote] = Field(
        default_factory=list,
        description="Per-module narrative notes.",
    )
    core_hubs: list[str] = Field(
        default_factory=list,
        description="Symbol ids that anchor the architecture.",
    )
    dead_code: list[str] = Field(
        default_factory=list,
        description="Symbol ids that appear unreachable.",
    )
    risks: list[str] = Field(default_factory=list, description="Architectural risks.")
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable follow-up recommendations.",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Generation metadata such as model name and timing.",
    )

    @model_validator(mode="after")
    def _check_unique_sections(self) -> ArchitectureDraft:
        module_names = [note.module for note in self.modules]
        duplicate_modules = sorted(
            name for name in set(module_names) if module_names.count(name) > 1
        )
        if duplicate_modules:
            raise ValueError(f"duplicate module notes: {duplicate_modules}")

        titles = [diagram.title for diagram in self.diagrams if diagram.title]
        duplicate_titles = sorted(title for title in set(titles) if titles.count(title) > 1)
        if duplicate_titles:
            raise ValueError(f"duplicate diagram titles: {duplicate_titles}")
        return self

    @property
    def primary_diagram(self) -> MermaidDiagram | None:
        """The first diagram, used as the document's headline figure."""
        return self.diagrams[0] if self.diagrams else None
