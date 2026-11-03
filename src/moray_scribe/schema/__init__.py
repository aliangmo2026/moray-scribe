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
from .sequence import (
    SequenceFlow,
    SequenceFlowDraft,
    SequenceMessage,
    SequenceMessageKind,
    SequenceParticipant,
    SequencePlan,
)
from .symbols import (
    AttrBinding,
    CallRef,
    DanglingCall,
    DynamicImportRef,
    ImportRef,
    RawSymbol,
    RawSymbolBundle,
    RouteInfo,
    SymbolKind,
    UrlPatternRef,
)

__all__ = [
    "ArchEdgeKind",
    "ArchitectureDraft",
    "AttrBinding",
    "CallRef",
    "DanglingCall",
    "DiagramKind",
    "Direction",
    "DynamicImportRef",
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
    "RouteInfo",
    "SequenceFlow",
    "SequenceFlowDraft",
    "SequenceMessage",
    "SequenceMessageKind",
    "SequenceParticipant",
    "SequencePlan",
    "StrictModel",
    "SymbolEdge",
    "SymbolGraph",
    "SymbolKind",
    "SymbolNode",
    "UrlPatternRef",
]
