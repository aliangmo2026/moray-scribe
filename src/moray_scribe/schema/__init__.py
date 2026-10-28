"""Public schema surface for moray-scribe.

Importing from ``moray_scribe.schema`` keeps downstream layers independent of the
file layout that defines the models.
"""

from __future__ import annotations

from .architecture import (
    ArchEdgeKind,
    ArchitectureDraft,
    DiagramKind,
    Direction,
    EdgeStyle,
    MermaidDiagram,
    MermaidEdge,
    MermaidNode,
    ModuleNote,
    NodeKind,
)
from .base import StrictModel
from .graph import EdgeKind, SymbolEdge, SymbolGraph, SymbolNode
from .symbols import CallRef, ImportRef, RawSymbol, RawSymbolBundle, SymbolKind

__all__ = [
    "ArchEdgeKind",
    "ArchitectureDraft",
    "CallRef",
    "DiagramKind",
    "Direction",
    "EdgeKind",
    "EdgeStyle",
    "ImportRef",
    "MermaidDiagram",
    "MermaidEdge",
    "MermaidNode",
    "ModuleNote",
    "NodeKind",
    "RawSymbol",
    "RawSymbolBundle",
    "StrictModel",
    "SymbolEdge",
    "SymbolGraph",
    "SymbolKind",
    "SymbolNode",
]
