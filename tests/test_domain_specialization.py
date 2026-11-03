"""Domain specialization: HTTP routes, ORM models and data-flow edges."""

from __future__ import annotations

import pytest

from moray_scribe.describer.prompts import SYSTEM_PROMPT
from moray_scribe.grapher import build_symbol_graph
from moray_scribe.grapher.serializer import to_prompt_view
from moray_scribe.renderer import draft_to_markdown
from moray_scribe.scanner import extract_source
from moray_scribe.scanner.domains import (
    is_dataflow_call,
    is_model_op_call,
    is_orm_base_text,
    is_orm_column_call,
    normalize_urlconf_path,
    parse_route_decorator,
    parse_urlconf_call,
)
from moray_scribe.schema import ArchitectureDraft, EdgeKind, RawSymbolBundle, SymbolGraph

# --------------------------------------------------------------------------
# Route detection
# --------------------------------------------------------------------------


def test_parse_route_flask_style() -> None:
    route = parse_route_decorator(["app.route('/login')"])
    assert route is not None
    assert route.path == "/login"
    assert route.methods == ["GET"]


def test_parse_route_methods_kwarg_is_normalized() -> None:
    route = parse_route_decorator(["bp.route('/u', methods=['post', 'GET']) "])
    assert route is not None
    assert route.methods == ["GET", "POST"]


@pytest.mark.parametrize(
    ("text", "methods"),
    [
        ("router.get('/items')", ["GET"]),
        ("api.post('/items/{id}')", ["POST"]),
        ("router.delete('/items/{id}')", ["DELETE"]),
        ("app.put('/x')", ["PUT"]),
        ("app.patch('/x')", ["PATCH"]),
    ],
)
def test_parse_route_verb_decorators(text: str, methods: list[str]) -> None:
    route = parse_route_decorator([text])
    assert route is not None
    assert route.methods == methods


def test_parse_route_method_list_decorators_have_no_path() -> None:
    # DRF/Django method decorators carry no URL path; Django maps paths in
    # urls.py instead, so these are NOT route sources (see urlconf tests).
    assert parse_route_decorator(["api_view(['GET', 'POST'])"]) is None
    assert parse_route_decorator(["require_http_methods(['POST'])"]) is None


@pytest.mark.parametrize(
    "decorator",
    [
        "cache.get('key')",  # verb name, non-path argument: not a route
        "property",
        "staticmethod",
        "app.before_request",
        "route_book.lookup('x')",
        "app.route(path_var)",
    ],
)
def test_parse_route_rejects_non_routes(decorator: str) -> None:
    assert parse_route_decorator([decorator]) is None


def test_parse_route_ignores_non_first_decorators_only_when_unmatchable() -> None:
    # A later decorator may still register a route (order is source order).
    assert parse_route_decorator(["login_required", "app.route('/x')"]) is not None


# --------------------------------------------------------------------------
# ORM detection helpers
# --------------------------------------------------------------------------


def test_is_orm_column_call_sqlalchemy_and_django() -> None:
    assert is_orm_column_call("Column")
    assert is_orm_column_call("db.Column")
    assert is_orm_column_call("mapped_column")
    assert is_orm_column_call("models.CharField")
    assert not is_orm_column_call("Field")  # pydantic, deliberately excluded
    assert not is_orm_column_call("print")


def test_is_orm_base_text() -> None:
    assert is_orm_base_text("models.Model")
    assert is_orm_base_text("db.Model")
    assert is_orm_base_text("mapped_as_dataclass[Base]") is False
    assert not is_orm_base_text("Base")


def test_is_dataflow_call_shapes() -> None:
    assert is_dataflow_call("session.commit")
    assert is_dataflow_call("db.session.query")
    assert is_dataflow_call("user.save")
    assert is_dataflow_call("self.delete")
    assert is_dataflow_call("session.execute")
    assert not is_dataflow_call("print")


def test_is_model_op_call_shapes() -> None:
    assert is_model_op_call("User.query.get")
    assert is_model_op_call("Order.objects.filter")
    assert not is_model_op_call("User")
    assert not is_model_op_call("User.name")


# --------------------------------------------------------------------------
# Scanner integration
# --------------------------------------------------------------------------

_ROUTE_SOURCE = '''
class Service:
    def run(self):
        return self.helper()

    def helper(self):
        return 1


def login():
    return True


app.route('/login')(login)
'''
# The decorator syntax cannot be expressed in a plain string easily, so the
# real integration test below uses decorator syntax in a module constant.


_ROUTE_MODULE = '''
def login():
    return "ok"


def index():
    return "index"
'''


def _bundle(source: str, module: str = "api") -> RawSymbolBundle:
    return extract_source(source, module=module, file=f"{module}.py")


def test_scanner_extracts_route_metadata() -> None:
    bundle = _bundle(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/login', methods=['GET', 'POST'])\n"
        "def login():\n"
        "    return 'ok'\n\n"
        "@app.route('/')\n"
        "def index():\n"
        "    return 'home'\n"
    )
    symbols = {symbol.qualname: symbol for symbol in bundle.symbols}
    assert symbols["login"].route is not None
    assert symbols["login"].route.path == "/login"
    assert symbols["login"].route.methods == ["GET", "POST"]
    assert symbols["index"].route.path == "/"
    assert symbols["index"].route.methods == ["GET"]


def test_scanner_route_is_none_without_decorator() -> None:
    bundle = _bundle("def plain():\n    return 1\n")
    assert bundle.symbols[0].route is None


def test_scanner_tags_sqlalchemy_model() -> None:
    bundle = _bundle(
        "from sqlalchemy import Column, Integer, String\n"
        "from sqlalchemy.orm import declarative_base\n"
        "Base = declarative_base()\n\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n"
        "    id = Column(Integer, primary_key=True)\n"
        "    name = Column(String)\n"
    )
    user = next(s for s in bundle.symbols if s.qualname == "User")
    assert "orm_model" in user.tags


def test_scanner_tags_django_model_by_base() -> None:
    bundle = _bundle(
        "from django.db import models\n\n"
        "class Book(models.Model):\n"
        "    title = models.CharField(max_length=100)\n"
    )
    book = next(s for s in bundle.symbols if s.qualname == "Book")
    assert "orm_model" in book.tags


def test_scanner_ignores_pydantic_field_as_orm() -> None:
    bundle = _bundle(
        "from pydantic import BaseModel, Field\n\n"
        "class Payload(BaseModel):\n"
        "    name: str = Field(min_length=1)\n"
    )
    payload = next(s for s in bundle.symbols if s.qualname == "Payload")
    assert "orm_model" not in payload.tags


def test_scanner_ignores_method_local_column_call() -> None:
    bundle = _bundle(
        "class NotAModel:\n"
        "    def build(self):\n"
        "        from sqlalchemy import Column\n"
        "        value = Column('x')\n"
        "        return value\n"
    )
    node = next(s for s in bundle.symbols if s.qualname == "NotAModel")
    assert "orm_model" not in node.tags


# --------------------------------------------------------------------------
# Graph integration
# --------------------------------------------------------------------------


def _graph(source: str, module: str = "app") -> SymbolGraph:
    return build_symbol_graph([_bundle(source, module)])


def test_graph_node_carries_route() -> None:
    graph = _graph(
        "def login():\n"
        "    return 'ok'\n\n"
        "app.route('/login')(login)\n"
    )
    # Decorator application form is a call, not a decorator; assert via syntax:
    graph = build_symbol_graph(
        [
            _bundle(
                "@app.route('/login')\n"
                "def login():\n"
                "    return 'ok'\n"
            )
        ]
    )
    node = graph.nodes["api.login"]
    assert node.route is not None
    assert node.route.path == "/login"
    assert node.is_entry is True  # route decorators are entry points


def test_graph_marks_orm_model_and_keeps_tag() -> None:
    graph = _graph(
        "from sqlalchemy.orm import declarative_base\n"
        "Base = declarative_base()\n\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n"
    )
    assert "orm_model" in graph.nodes["app.User"].tags


def test_graph_emits_dataflow_for_model_query() -> None:
    graph = _graph(
        "class User:\n"
        "    __tablename__ = 'users'\n\n"
        "    def save(self):\n"
        "        return 1\n\n\n"
        "def load(user_id):\n"
        "    return User.query.get(user_id)\n"
    )
    df = {(e.source, e.target) for e in graph.edges if e.kind is EdgeKind.DATA_FLOW}
    assert ("app.load", "app.User") in df


def test_graph_emits_dataflow_for_resolved_write_call() -> None:
    graph = _graph(
        "class Repo:\n"
        "    def save(self):\n"
        "        return 1\n\n\n"
        "def persist(repo):\n"
        "    repo.save()\n"
    )
    # `repo.save()` cannot resolve statically (parameter type unknown), so no
    # data-flow edge is fabricated — conservatism holds.
    assert not [e for e in graph.edges if e.kind is EdgeKind.DATA_FLOW]


def test_graph_emits_dataflow_for_self_attribute_write() -> None:
    graph = _graph(
        "class Session:\n"
        "    def commit(self):\n"
        "        return 1\n\n\n"
        "class Unit:\n"
        "    def __init__(self):\n"
        "        self.session = Session()\n\n"
        "    def flush(self):\n"
        "        self.session.commit()\n"
    )
    df = {(e.source, e.target) for e in graph.edges if e.kind is EdgeKind.DATA_FLOW}
    assert ("app.Unit.flush", "app.Session.commit") in df


def test_graph_has_no_dataflow_for_plain_calls() -> None:
    graph = _graph(
        "def helper():\n"
        "    return 1\n\n\n"
        "def caller():\n"
        "    return helper()\n"
    )
    assert not [e for e in graph.edges if e.kind is EdgeKind.DATA_FLOW]


# --------------------------------------------------------------------------
# Prompt payload
# --------------------------------------------------------------------------


def test_prompt_view_includes_route_and_orm_flags() -> None:
    graph = build_symbol_graph(
        [
            _bundle(
                "@app.route('/orders', methods=['POST'])\n"
                "def create_order():\n"
                "    return 'ok'\n\n"
                "class Order:\n"
                "    __tablename__ = 'orders'\n",
                "app",
            )
        ]
    )
    # The model class has no inbound references yet, so it is a dead-code
    # candidate; the prompt view drops those unless asked to keep them.
    view = to_prompt_view(graph, include_dead=True)
    by_id = {item["i"]: item for item in view["n"]}
    assert by_id["app.create_order"]["r"] == "POST /orders"
    assert by_id["app.Order"]["o"] == 1


def test_prompt_documents_domain_fields() -> None:
    assert 'r="METHODS path"' in SYSTEM_PROMPT
    assert '"o": 1' in SYSTEM_PROMPT or "o=1" in SYSTEM_PROMPT or "o: 1" in SYSTEM_PROMPT
    assert "data_flow" in SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------


def _empty_draft() -> ArchitectureDraft:
    return ArchitectureDraft.model_validate(
        {
            "title": "T",
            "summary": "S" * 60,
            "diagrams": [],
            "modules": [],
        }
    )


def _render(graph: SymbolGraph) -> str:
    return draft_to_markdown(
        _empty_draft(),
        graph=graph,
        full_graph=graph,
        known_gaps=[],
        dead_code=[],
        entry_points=[],
        core_hubs=[],
        project_name="demo",
    )


def test_markdown_route_table() -> None:
    graph = build_symbol_graph(
        [
            _bundle(
                "@app.route('/login', methods=['GET', 'POST'])\n"
                "def login():\n"
                "    return 'ok'\n",
                "app",
            )
        ]
    )
    md = _render(graph)
    assert "## 七、API 路由表【引擎统计】" in md
    assert "| GET/POST | `/login` | `app.login` | app |" in md


def test_markdown_route_section_reports_absence() -> None:
    graph = _graph("def plain():\n    return 1\n")
    md = _render(graph)
    assert "未检测到 HTTP 路由" in md


def test_markdown_dataflow_section_renders_mermaid() -> None:
    graph = _graph(
        "class User:\n"
        "    __tablename__ = 'users'\n\n\n"
        "def load(user_id):\n"
        "    return User.query.get(user_id)\n"
    )
    md = _render(graph)
    assert "## 八、数据流向图【引擎统计】" in md
    assert "flowchart TD" in md
    assert "==>" in md  # thick data-flow arrow
    assert 'classDef database' in md  # ORM model drawn as a database node


def test_markdown_dataflow_section_reports_absence() -> None:
    graph = _graph("def plain():\n    return 1\n")
    md = _render(graph)
    assert "未检测到静态可确认的 ORM/会话数据流" in md


# --------------------------------------------------------------------------
# Django urlconf mapping
# --------------------------------------------------------------------------


def test_parse_urlconf_call_function_view() -> None:
    assert parse_urlconf_call("path", "'login/', views.login") == ("login/", "views.login")


def test_parse_urlconf_call_class_view_keeps_call_syntax() -> None:
    parsed = parse_urlconf_call("re_path", r"'^books/$', BookView.as_view()")
    assert parsed == (r"^books/$", "BookView.as_view()")


def test_parse_urlconf_rejects_non_urlconf_calls() -> None:
    assert parse_urlconf_call("print", "'x', views.login") is None
    assert parse_urlconf_call("path", "x, views.login") is None


def test_normalize_urlconf_path() -> None:
    assert normalize_urlconf_path("login/") == "/login/"
    assert normalize_urlconf_path("/login/") == "/login/"
    assert normalize_urlconf_path("") == "/"


def test_graph_accepts_empty_urlconf_path() -> None:
    """`path("", views.home)` is a valid root route and must not break scanning."""
    views = extract_source(
        "def home(request):\n    return None\n",
        module="app.views",
        file="app/views.py",
    )
    urls = extract_source(
        "from app import views\n"
        "urlpatterns = [path('', views.home)]\n",
        module="app.urls",
        file="app/urls.py",
    )
    graph = build_symbol_graph([views, urls])
    route = graph.nodes["app.views.home"].route
    assert route is not None
    assert route.path == "/"


def test_graph_attaches_django_urlconf_route_to_view() -> None:
    views = extract_source(
        "def login(request):\n    return None\n",
        module="app.views",
        file="app/views.py",
    )
    urls = extract_source(
        "from django.urls import path\n"
        "from app import views\n\n"
        "urlpatterns = [\n"
        "    path('login/', views.login),\n"
        "]\n",
        module="app.urls",
        file="app/urls.py",
    )
    graph = build_symbol_graph([views, urls])
    node = graph.nodes["app.views.login"]
    assert node.route is not None
    assert node.route.path == "/login/"
    assert node.route.methods == ["ANY"]
    assert node.is_entry is True


def test_graph_attaches_django_urlconf_route_to_class_view() -> None:
    views = extract_source(
        "class BookView:\n"
        "    def get(self, request):\n"
        "        return None\n\n"
        "    @classmethod\n"
        "    def as_view(cls):\n"
        "        return cls()\n",
        module="app.views",
        file="app/views.py",
    )
    urls = extract_source(
        "from django.urls import path\n"
        "import app.views as views\n\n"
        "urlpatterns = [path('books/', views.BookView.as_view())]\n",
        module="app.urls",
        file="app/urls.py",
    )
    graph = build_symbol_graph([views, urls])
    assert graph.nodes["app.views.BookView"].route is not None


def test_urlconf_does_not_override_decorator_route() -> None:
    views = extract_source(
        "@app.route('/decorated')\n"
        "def handler():\n"
        "    return None\n",
        module="app.views",
        file="app/views.py",
    )
    urls = extract_source(
        "from app import views\n"
        "urlpatterns = [path('urlconf/', views.handler)]\n",
        module="app.urls",
        file="app/urls.py",
    )
    graph = build_symbol_graph([views, urls])
    route = graph.nodes["app.views.handler"].route
    assert route is not None
    assert route.path == "/decorated"
