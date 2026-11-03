"""Resolve Django urlconf view expressions to graph nodes.

``path("login/", views.login)`` names the view through a module alias, so the
resolution reuses the builder's alias/symbol indexes. Class-based views appear
as ``LoginView.as_view``; the ``as_view`` hop is stripped because the route
target is the class itself. Anything unresolvable returns ``None`` rather than
guessing (conservatism rule §2).
"""

from __future__ import annotations

from moray_scribe.schema import SymbolGraph

_AS_VIEW = ".as_view"


def resolve_url_pattern(
    module: str,
    target: str,
    idx: object,
    graph: SymbolGraph,
) -> str | None:
    """Resolve a urlconf view expression to a node id, or ``None``.

    ``idx`` is the builder's ``_Indexes`` (duck-typed to stay cycle-free).
    """
    chain = target.strip()
    if chain.endswith("()"):
        chain = chain[:-2]
    if chain.endswith(_AS_VIEW):
        chain = chain[: -len(_AS_VIEW)]
    if not chain:
        return None

    segments = chain.split(".")
    if len(segments) == 1:
        found = idx.sym(module, segments[0])  # type: ignore[attr-defined]
        if found is None:
            found = idx.cls(module, segments[0])  # type: ignore[attr-defined]
        if found is None:
            entry = idx.alias(module, segments[0])  # type: ignore[attr-defined]
            if entry is not None and entry[1] is not None:
                found = idx.sym(entry[0], entry[1])  # type: ignore[attr-defined]
        return found if found in graph.nodes else None

    # Dotted view chain: resolve the module prefix through the alias table.
    root = segments[0]
    rest = ".".join(segments[1:])
    entry = idx.alias(module, root)  # type: ignore[attr-defined]
    found: str | None = None
    if entry is not None:
        target_module, original = entry
        if original is None:
            # `import app.views as views`: the alias binds the module itself.
            base_module = target_module
        else:
            # `from app import views`: the alias binds a submodule when the
            # dotted candidate is a known module, otherwise the object itself.
            candidate = f"{target_module}.{original}"
            base_module = candidate if candidate in idx.modules else target_module  # type: ignore[attr-defined]
        found = idx.sym(base_module, rest)  # type: ignore[attr-defined]
    else:
        # Fully-qualified expression: `app.views.login`.
        base_module = ".".join(segments[:-1])
        found = idx.sym(base_module, segments[-1])  # type: ignore[attr-defined]
    return found if found in graph.nodes else None
