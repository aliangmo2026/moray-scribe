"""Domain-specific detection helpers: HTTP routes and ORM data flow.

Pure, source-text-level heuristics kept separate from the libcst visitor so
each rule is unit-testable without building a tree. The conservatism rule
(§2) applies unchanged: a recognizer must be *sure* from the decorator text
alone, otherwise it returns ``None`` and the site stays generic.
"""

from __future__ import annotations

import re

from moray_scribe.schema import RouteInfo

# Decorator names that carry an HTTP path, keyed by the default method(s).
_HTTP_VERBS: dict[str, tuple[str, ...]] = {
    "get": ("GET",),
    "post": ("POST",),
    "put": ("PUT",),
    "patch": ("PATCH",),
    "delete": ("DELETE",),
    "head": ("HEAD",),
    "options": ("OPTIONS",),
}

# ``@app.route`` style: method-agnostic, defaults to GET unless ``methods=``.
_ROUTE_NAMES = frozenset({"route", "add_url_rule"})

# ``@api_view(["GET", "POST"])`` (DRF) and ``@require_http_methods([...])``.
_METHOD_LIST_NAMES = frozenset({"api_view", "require_http_methods"})

# First string argument of a route decorator must be a ``/``-rooted URL path.
# This rejects ``@cache.get("key")`` and similar non-route ``get`` decorators.
_PATH_PATTERN = re.compile(r"^/")

# ``methods=["GET", "POST"]`` keyword inside the decorator call arguments.
_METHODS_KWARG = re.compile(r"methods\s*=\s*\[([^\]]*)\]")

_STRING_LITERAL = re.compile(r"""['"]([^'"]*)['"]""")

# Urlconf helpers and the second-argument view chain (``views.login``,
# ``LoginView.as_view``). The chain stops at the first ``(`` so ``as_view()``
# keeps its last segment.
_URLCONF_CALLS = frozenset({"path", "re_path", "url"})
# Public alias: the scanner uses it as a fast reject before rendering call args.
URLCONF_NAMES = _URLCONF_CALLS
_CALL_ARG_CHAIN = re.compile(
    r"""['"][^'"]*['"]\s*,\s*([A-Za-z_][\w.]*(?:\(\))?)"""
)

_DECORATOR_CALL = re.compile(r"^([A-Za-z_][\w.]*)\s*\((.*)\)\s*$", re.DOTALL)

# Call chains that read or write through an ORM session/connection layer.
_SESSION_ROOTS = frozenset({"session", "db.session", "conn", "connection", "cursor", "transaction"})
_SESSION_OPS = frozenset(
    {
        "query", "execute", "scalar", "fetchall", "fetchone", "get",
        "add", "add_all", "merge", "flush", "commit", "rollback", "delete", "refresh",
    }
)
# Write-shaped ops that mark a data flow even for an unknown receiver.
_WRITE_OPS = frozenset({"save", "commit", "delete"})
# Read/write ops reachable on an ORM model class itself (Flask-SQLAlchemy style).
_MODEL_OPS = frozenset(
    {"query", "query.get", "query.filter", "query.filter_by", "query.first",
     "query.all", "query.get_or_404", "objects", "objects.get", "objects.filter",
     "objects.create", "objects.update", "objects.delete"}
)
# Calls that instantiate an ORM column/mapping inside a class body.
# NOTE: pydantic's ``Field`` is deliberately NOT here — too many false positives.
_ORM_COLUMN_CALLS = frozenset({"Column", "mapped_column", "relationship"})
# Django model field constructors (``models.CharField`` etc.): suffix-matched.
_DJANGO_FIELD_CALLS = frozenset(
    {
        "CharField", "TextField", "IntegerField", "BooleanField", "DateTimeField",
        "DateField", "FloatField", "DecimalField", "ForeignKey", "ManyToManyField",
        "OneToOneField", "AutoField", "JSONField", "SlugField", "EmailField",
        "UUIDField", "BigAutoField",
    }
)


def is_orm_base_text(base_text: str) -> bool:
    """Whether a class base expression proves an ORM model (Django ``models.Model``)."""
    name = base_text.split("[", 1)[0].strip()
    return name.endswith("models.Model") or name.endswith("db.Model")


def _extract_methods(name: str, args_text: str) -> tuple[str, ...] | None:
    """Methods for one recognized decorator name, or None when unrecognized."""
    if name.split(".")[-1] in _ROUTE_NAMES:
        match = _METHODS_KWARG.search(args_text)
        if match:
            found = _STRING_LITERAL.findall(match.group(1))
            return tuple(sorted({m.upper() for m in found if m})) or None
        return ("GET",)
    verb = name.split(".")[-1].lower()
    if verb in _HTTP_VERBS:
        return _HTTP_VERBS[verb]
    if name.split(".")[-1] in _METHOD_LIST_NAMES:
        match = re.search(r"\[([^\]]*)\]", args_text)
        if match:
            found = _STRING_LITERAL.findall(match.group(1))
            return tuple(sorted({m.upper() for m in found if m})) or None
        return None
    return None


def parse_route_decorator(decorators: list[str]) -> RouteInfo | None:
    """Detect an HTTP route decorator from the rendered decorator source texts.

    Recognized shapes (path must start with ``/``):

    - ``@app.route('/login')``, ``@bp.route('/u', methods=['GET','POST'])``
    - ``@router.get('/items')`` and the other HTTP verbs (Flask/FastAPI style)
    - ``@api_view(['GET'])`` / ``@require_http_methods(['POST'])`` (Django/DRF)

    Returns ``None`` for anything else — including verb decorators whose first
    argument is not a ``/``-rooted string, which keeps ``@cache.get("key")``
    and friends out of the route table.
    """
    for text in decorators:
        match = _DECORATOR_CALL.match(text.strip())
        if match is None:
            continue
        name, args_text = match.group(1), match.group(2)
        methods = _extract_methods(name, args_text)
        if methods is None:
            continue
        path_match = _STRING_LITERAL.search(args_text)
        if path_match is None:
            continue
        path = path_match.group(1)
        if not _PATH_PATTERN.match(path):
            continue
        return RouteInfo(path=path, methods=list(methods), decorator=text.strip())
    return None


def parse_urlconf_call(callee: str, args_text: str) -> tuple[str, str] | None:
    """Detect a Django urlconf entry: ``path("login/", views.login)``.

    Returns ``(path, view_chain)`` or ``None``. Only the three urlconf helpers
    are accepted, the first argument must be a string literal, and the second
    must be a name/attribute chain (optionally ending in ``.as_view``). The
    path keeps its source spelling; Django's leading-slash convention is
    normalized by :func:`normalize_urlconf_path`.
    """
    if callee.split(".")[-1] not in _URLCONF_CALLS:
        return None
    path_match = _STRING_LITERAL.search(args_text)
    view_match = _CALL_ARG_CHAIN.search(args_text)
    if path_match is None or view_match is None:
        return None
    path = path_match.group(1)
    view = view_match.group(1)
    if not path and callee.split(".")[-1] != "path":
        return None
    if not view:
        return None
    return path, view


def normalize_urlconf_path(path: str) -> str:
    """Normalize a Django urlconf pattern to a ``/``-rooted display path."""
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def is_dataflow_call(callee: str) -> bool:
    """Whether a resolved call chain plausibly moves data through an ORM layer.

    Two accepted shapes, both requiring the chain to be *statically resolved*
    before this test runs (the builder only consults it on resolved targets):

    - receiver root is a session-like object (``session``, ``db.session``,
      ``conn``...) and the last segment is a session op (``commit``, ``query``...)
    - the last segment is a write-shaped op (``save``, ``commit``, ``delete``)
      on any receiver — ``user.save()`` marks a write regardless of the type.
    """
    chain = callee.strip()
    if not chain:
        return False
    root, _, last = chain.rpartition(".")
    last = last.split("[", 1)[0]
    if not last:
        return False
    if root in _SESSION_ROOTS and last in _SESSION_OPS:
        return True
    return last in _WRITE_OPS


def is_model_op_call(callee: str) -> bool:
    """Whether the chain is ``<Model>.query.get(...)`` style ORM access.

    ``root`` must be resolved to an in-graph class by the caller; only the op
    part is validated here (``User.query.get``, ``Order.objects.filter``).
    """
    chain = callee.strip()
    root, _, rest = chain.partition(".")
    if not root or not rest:
        return False
    rest = rest.split("(", 1)[0].rstrip(".")
    if rest in _MODEL_OPS:
        return True
    # ``session.query(User)``-style is handled by is_dataflow_call; here accept
    # ``Model.query.get`` variants with a trailing method call.
    parts = rest.split(".")
    return len(parts) >= 2 and parts[0] == "query" and parts[-1] in _SESSION_OPS


def is_orm_column_call(chain: str) -> bool:
    """Whether a class-body call instantiates an ORM column/mapping.

    SQLAlchemy style matches the whole chain or its last segment against the
    column set (``Column``, ``db.Column``, ``sa.mapped_column``); Django style
    suffix-matches the field constructors (``models.CharField``).
    """
    if chain in _ORM_COLUMN_CALLS:
        return True
    last = chain.split(".")[-1]
    return last in _ORM_COLUMN_CALLS or last in _DJANGO_FIELD_CALLS
