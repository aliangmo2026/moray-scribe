"""Symbol graph construction, analysis, pruning and prompt serialization."""

from __future__ import annotations

from .analyzer import (
    annotate_degrees,
    compute_pagerank,
    detect_dead_code,
    detect_entry_points,
)
from .builder import build_symbol_graph
from .pruner import prune, prune_for_prompt
from .serializer import to_prompt_view
from .tracer import CallChain, ChainStep, chain_step_arrows, trace_call_chains

__all__ = [
    "CallChain",
    "ChainStep",
    "annotate_degrees",
    "build_symbol_graph",
    "chain_step_arrows",
    "compute_pagerank",
    "detect_dead_code",
    "detect_entry_points",
    "prune",
    "prune_for_prompt",
    "to_prompt_view",
    "trace_call_chains",
]
