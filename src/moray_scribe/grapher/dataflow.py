"""Deterministic data-flow edge resolution for ORM-shaped call chains.

The scanner recognizes ORM *shapes* (:mod:`moray_scribe.scanner.domains`); this
module decides when such a shape is attachable to a concrete graph node. The
conservatism rule holds: a data-flow edge is emitted only when the receiver or
the resolved target is 100% identifiable from static evidence — everything else
stays in the LLM inference pool instead of becoming a guessed edge.

Confirmed shapes:

1. A call whose chain already resolved to a graph node **and** matches a
   session/write op (``session.commit``, ``user.save``) → data flow to that node.
2. ``User.query.get(...)`` / ``User.objects.filter(...)`` where ``User`` is an
   in-graph class (same module or a from-import alias) → data flow to the class.
"""

from __future__ import annotations

from moray_scribe.scanner.domains import is_dataflow_call, is_model_op_call
from moray_scribe.schema import SymbolGraph


def resolve_model_op(
    module: str,
    callee: str,
    idx: object,
    graph: SymbolGraph,
) -> str | None:
    """Resolve a ``Model.query...``/``Model.objects...`` chain to the model class.

    ``idx`` is the builder's ``_Indexes`` (``cls``/``alias`` lookups); it is
    duck-typed here so this module stays import-cycle free. Returns ``None``
    whenever the receiver class is not positively identified.
    """
    root, _, rest = callee.partition(".")
    if not root or not rest or not is_model_op_call(callee):
        return None
    class_id = idx.cls(module, root)  # type: ignore[attr-defined]
    if class_id is None:
        entry = idx.alias(module, root)  # type: ignore[attr-defined]
        if entry is not None and entry[1] is not None:
            class_id = idx.cls(entry[0], entry[1])  # type: ignore[attr-defined]
    if class_id is not None and class_id in graph.nodes:
        return class_id
    return None


def is_dataflow_shape(callee: str) -> bool:
    """Public re-export used by the builder on already-resolved call chains."""
    return is_dataflow_call(callee)
