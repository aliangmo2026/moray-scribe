"""Tests for the graph-RAG retrieval used by /api/chat (subgraph + slices)."""

from __future__ import annotations

import json
from pathlib import Path

from web_viewer.retrieval import (
    RetrievalIndex,
    select_subgraph,
    tokenize_question,
)


def _node(node_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": node_id,
        "name": node_id.rsplit(".", 1)[-1],
        "kind": "function",
        "module": node_id.split(".")[0],
        "file": f"{node_id.split('.')[0]}.py",
        "lineno": 1,
        "end_lineno": 2,
        "pagerank": 0.0,
    }
    payload.update(overrides)
    return payload


def _graph_payload(
    nodes: list[dict[str, object]],
    edges: list[tuple[str, str]],
    *,
    root: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "nodes": {str(node["id"]): node for node in nodes},
        "edges": [
            {"source": src, "target": dst, "kind": "calls", "weight": 1.0}
            for src, dst in edges
        ],
        "metadata": {"project": "demo"},
    }
    if root is not None:
        payload["metadata"]["root"] = root  # type: ignore[index]
    return payload


def _login_graph() -> dict[str, object]:
    """route -> handler -> service -> db helper chain plus an unrelated island."""
    return _graph_payload(
        [
            _node("app.login_route", route={"path": "/login", "methods": ["POST"]}, pagerank=0.4),
            _node("app.check_password", docstring="校验用户登录凭据", pagerank=0.3),
            _node("db.fetch_user", pagerank=0.2),
            _node("db.write_session", pagerank=0.1),
            _node("util.format_date", pagerank=0.05),
        ],
        [
            ("app.login_route", "app.check_password"),
            ("app.check_password", "db.fetch_user"),
            ("app.check_password", "db.write_session"),
        ],
    )


def test_tokenize_question_ascii_and_cjk() -> None:
    tokens = tokenize_question("Fix the login 逻辑 after 修改用户登录!")

    assert "login" in tokens
    assert "fix" in tokens
    # CJK runs become overlapping bigrams so partial terms still match.
    assert "登录" in tokens
    assert "用户" in tokens


def test_seed_selection_matches_id_name_route_and_docstring() -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    seeds = index.seed_nodes("login")
    assert seeds and seeds[0] == "app.login_route"

    doc_seeds = index.seed_nodes("登录")
    assert "app.check_password" in doc_seeds

    route_seeds = index.seed_nodes("/login")
    assert seeds and "app.login_route" in route_seeds


def test_reverse_bfs_includes_callers() -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    result = select_subgraph(index, "write_session", reverse_hops=2, forward_hops=1)

    assert "db.write_session" in result.node_ids      # seed
    assert "app.check_password" in result.node_ids    # reverse hop 1
    assert "app.login_route" in result.node_ids       # reverse hop 2
    assert result.seeds == ["db.write_session"]


def test_forward_hop_adds_callees_only() -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    result = select_subgraph(index, "check_password", reverse_hops=0, forward_hops=1)

    assert "app.check_password" in result.node_ids
    assert "db.fetch_user" in result.node_ids
    assert "app.login_route" not in result.node_ids


def test_budget_caps_expansion_by_pagerank() -> None:
    nodes = [_node(f"m.f{index:02d}", pagerank=1.0 / (index + 1)) for index in range(30)]
    nodes.append(_node("m.target", pagerank=0.5))
    edges = [(f"m.f{index:02d}", "m.target") for index in range(30)]
    index = RetrievalIndex(_graph_payload(nodes, edges), source_root=None)

    result = select_subgraph(index, "target", max_nodes=10, reverse_hops=2)

    assert len(result.node_ids) == 10
    # Higher-pagerank callers fill the budget first.
    assert "m.f00" in result.node_ids
    assert "m.f29" not in result.node_ids


def test_no_match_yields_empty_result() -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    result = select_subgraph(index, "量子纠缠完全无关词")

    assert result.node_ids == []
    assert result.edges == []
    assert result.slices == []


def test_edges_induced_and_deterministic() -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    result = select_subgraph(index, "login")
    again = select_subgraph(index, "login")

    assert again.node_ids == result.node_ids
    assert again.edges == result.edges
    keys = {(edge["source"], edge["target"]) for edge in result.edges}
    assert ("app.login_route", "app.check_password") in keys
    # Induced only: the util island never appears.
    assert all("util" not in edge["source"] for edge in result.edges)


def test_source_slices_read_guarded_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "\n".join(f"line {number}" for number in range(1, 31)), encoding="utf-8"
    )
    payload = _graph_payload(
        [_node("app.check_password", file="app.py", lineno=10)],
        [],
        root=str(tmp_path),
    )
    index = RetrievalIndex(payload, source_root=tmp_path)

    result = select_subgraph(index, "check_password")

    assert len(result.slices) == 1
    sl = result.slices[0]
    assert sl["file"] == "app.py"
    assert sl["start"] == 7
    assert "line 10" in sl["lines"]


def test_source_slices_reject_traversal(tmp_path: Path) -> None:
    secret = tmp_path.parent / "secret.py"
    payload = _graph_payload(
        [_node("evil.thing", file="../secret.py", lineno=1)],
        [],
        root=str(tmp_path),
    )
    index = RetrievalIndex(payload, source_root=tmp_path)

    result = select_subgraph(index, "thing")

    assert result.slices == []
    assert secret.exists() is False


def test_malformed_graph_payload_is_tolerated() -> None:
    index = RetrievalIndex({"nodes": "nope", "edges": None}, source_root=None)  # type: ignore[arg-type]

    assert index.seed_nodes("anything") == []
    result = select_subgraph(index, "anything")
    assert result.node_ids == []


def test_seed_caps_at_six_and_prefers_score() -> None:
    nodes = [
        _node(f"m.login{index}", pagerank=0.01) for index in range(10)
    ]
    index = RetrievalIndex(_graph_payload(nodes, []), source_root=None)

    seeds = index.seed_nodes("login")

    assert len(seeds) == 6
    assert seeds[0] == "m.login0"  # tie broken by id


def test_slices_capped(tmp_path: Path) -> None:
    for number in range(6):
        (tmp_path / f"m{number}.py").write_text("x = 1\n", encoding="utf-8")
    nodes = [_node(f"m{number}.f", file=f"m{number}.py", lineno=1) for number in range(6)]
    index = RetrievalIndex(_graph_payload(nodes, [], root=str(tmp_path)), source_root=tmp_path)

    result = select_subgraph(index, "f", max_slices=3)

    assert len(result.slices) == 3


def test_context_payload_compact_shape(tmp_path: Path) -> None:
    index = RetrievalIndex(_login_graph(), source_root=None)

    result = select_subgraph(index, "login")
    payload = result.to_context(max_context_nodes=24)

    data = json.loads(json.dumps(payload))
    assert {"nodes", "edges", "slices"} <= set(data)
    assert data["nodes"] and "id" in data["nodes"][0]
    assert data["slices"] == []  # no source root configured
