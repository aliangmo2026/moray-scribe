"""Tests for the sequence-describe stage (LLM narration + engine fallback)."""

from __future__ import annotations

import json
from typing import Any

from moray_scribe.config import Settings
from moray_scribe.describer.sequencer import (
    SEQUENCE_SYSTEM_PROMPT,
    SequenceOutcome,
    build_sequence_prompt,
    describe_sequences,
    engine_sequence_flow,
)
from moray_scribe.exceptions import DescriberError
from moray_scribe.schema import EdgeKind, SymbolEdge, SymbolGraph, SymbolKind, SymbolNode


def _node(symbol_id: str, kind: SymbolKind = SymbolKind.FUNCTION) -> SymbolNode:
    return SymbolNode(
        id=symbol_id,
        name=symbol_id.rsplit(".", 1)[-1],
        qualname=symbol_id,
        kind=kind,
        module=symbol_id.split(".")[0],
        file=f"{symbol_id.split('.')[0]}.py",
        lineno=1,
        end_lineno=2,
    )


def _chain_graph() -> SymbolGraph:
    return SymbolGraph(
        nodes={
            "app.main": _node("app.main"),
            "core.Engine": _node("core.Engine", SymbolKind.CLASS),
            "core.build": _node("core.build"),
            "db.save": _node("db.save"),
        },
        edges=[
            SymbolEdge(source="app.main", target="core.Engine", kind=EdgeKind.INSTANTIATES),
            SymbolEdge(source="core.Engine", target="core.build", kind=EdgeKind.CALLS),
            SymbolEdge(source="core.build", target="db.save", kind=EdgeKind.CALLS),
        ],
    )


def _chains() -> list[Any]:
    from moray_scribe.grapher.tracer import trace_call_chains

    graph = _chain_graph()
    graph.nodes["app.main"].is_entry = True
    chains = trace_call_chains(graph, max_depth=4)
    assert len(chains) == 1
    return chains


def _plan_json(titles: list[str]) -> str:
    flows = []
    for index, title in enumerate(titles):
        flows.append(
            {
                "title": title,
                "summary": f"流程{index}的概述。",
                "participants": [
                    {"key": "App", "label": "app 模块"},
                    {"key": "Core", "label": "core 模块"},
                ],
                "messages": [
                    {"sender": "App", "receiver": "Core", "label": "run()", "kind": "sync"},
                    {"sender": "Core", "receiver": "App", "label": "完成", "kind": "reply"},
                ],
            }
        )
    return json.dumps({"flows": flows}, ensure_ascii=False)


def _cfg(**updates: object) -> Settings:
    payload: dict[str, object] = {"_env_file": None, "max_retries": 0}
    payload.update(updates)
    return Settings(**payload)  # type: ignore[arg-type]


def test_prompt_is_deterministic_and_mentions_chains() -> None:
    graph = _chain_graph()
    chains = _chains()

    prompt = build_sequence_prompt(chains, graph, project_name="demo")
    again = build_sequence_prompt(chains, graph, project_name="demo")

    assert prompt == again
    assert "app.main" in prompt
    assert "core.Engine" in prompt
    assert "instantiates" in prompt


def test_system_prompt_requires_json_and_order() -> None:
    assert "flows" in SEQUENCE_SYSTEM_PROMPT
    assert "sequenceDiagram" in SEQUENCE_SYSTEM_PROMPT
    assert "SAME ORDER" in SEQUENCE_SYSTEM_PROMPT


def test_describe_happy_path(
    fake_ollama_client: Any,
) -> None:
    graph = _chain_graph()
    chains = _chains()
    client = fake_ollama_client([_plan_json(["启动流程"])])

    outcome = describe_sequences(chains, client, _cfg(), graph=graph, project_name="demo")

    assert isinstance(outcome, SequenceOutcome)
    assert outcome.degraded is False
    assert len(outcome.flows) == 1
    assert outcome.flows[0].entry == "app.main"
    assert outcome.flows[0].source == "ai"
    assert outcome.flows[0].title == "启动流程"
    assert client.backend.call_log[0]["format"] == "json"


def test_validator_rejects_wrong_flow_count_then_retries(
    fake_ollama_client: Any,
) -> None:
    graph = _chain_graph()
    chains = _chains()
    # chains == 1, but first answer carries two flows -> semantic rejection.
    client = fake_ollama_client([_plan_json(["多了", "再多"]), _plan_json(["启动流程"])])

    outcome = describe_sequences(chains, client, _cfg(max_retries=2), graph=graph)

    assert outcome.degraded is False
    assert [flow.title for flow in outcome.flows] == ["启动流程"]
    assert client.backend.call_log[0]["format"] == "json"


def _multi_entry_graph() -> SymbolGraph:
    """Three entries -> three independent chains for ladder testing."""
    graph = _chain_graph()
    graph.add_node(_node("api.handle"))
    graph.add_node(_node("job.tick"))
    graph.add_edge(
        SymbolEdge(source="api.handle", target="db.save", kind=EdgeKind.CALLS)
    )
    graph.add_edge(
        SymbolEdge(source="job.tick", target="core.build", kind=EdgeKind.CALLS)
    )
    graph.nodes["api.handle"].is_entry = True
    graph.nodes["job.tick"].is_entry = True
    return graph


def test_ladder_shrinks_chain_count_before_giving_up(
    fake_ollama_client: Any,
) -> None:
    graph = _multi_entry_graph()
    chains = trace_all(graph)
    assert len(chains) == 3
    # Rung 1 (3 chains) gets garbage; rung 2 (2 chains) gets a valid 2-flow plan.
    client = fake_ollama_client(["绝对不是 JSON", _plan_json(["链一", "链二"])])

    outcome = describe_sequences(chains, client, _cfg(), graph=graph)

    assert outcome.degraded is False
    assert len(outcome.flows) == 2
    assert outcome.rejected_rungs == 1


def test_all_rungs_failing_falls_back_to_engine_flows(
    fake_ollama_client: Any,
) -> None:
    graph = _chain_graph()
    chains = _chains()
    client = fake_ollama_client(["garbage"])

    outcome = describe_sequences(chains, client, _cfg(), graph=graph)

    assert outcome.degraded is True
    assert len(outcome.flows) == len(chains)
    assert all(flow.source == "engine" for flow in outcome.flows)
    assert outcome.flows[0].entry == chains[0].entry


def test_transport_failure_still_degrades(fake_ollama_client: Any) -> None:
    graph = _chain_graph()
    chains = _chains()
    client = fake_ollama_client([ConnectionError("refused")])

    outcome = describe_sequences(chains, client, _cfg(), graph=graph)

    assert outcome.degraded is True


def test_empty_chains_short_circuits(fake_ollama_client: Any) -> None:
    client = fake_ollama_client([])

    outcome = describe_sequences([], client, _cfg(), graph=_chain_graph())

    assert outcome.flows == []
    assert outcome.degraded is False
    assert client.backend.call_log == []


def test_engine_sequence_flow_shape() -> None:
    graph = _chain_graph()
    chains = _chains()

    flow = engine_sequence_flow(chains[0], graph)

    assert flow.entry == "app.main"
    assert flow.source == "engine"
    keys = {participant.key for participant in flow.participants}
    assert keys == {"app", "core", "db"}
    labels = {participant.label for participant in flow.participants}
    assert {"app", "core", "db"} <= labels
    assert flow.messages[0].sender == "app"
    assert flow.messages[0].receiver == "core"
    assert "Engine()" in flow.messages[0].label
    assert flow.messages[-1].receiver == "db"


def test_engine_flow_is_deterministic() -> None:
    graph = _chain_graph()
    chains = _chains()

    first = engine_sequence_flow(chains[0], graph).model_dump_json()
    second = engine_sequence_flow(chains[0], graph).model_dump_json()

    assert first == second


def test_engine_flow_handles_missing_graph_nodes() -> None:
    from moray_scribe.grapher.tracer import CallChain, ChainStep

    chain = CallChain(
        entry="ghost.main",
        steps=[ChainStep(src="ghost.main", dst="gone.method", kind="calls", depth=0)],
    )

    flow = engine_sequence_flow(chain, SymbolGraph())

    assert flow.participants  # module fallback, never crashes
    assert flow.messages[0].receiver in {step.key for step in flow.participants}


def trace_all(graph: SymbolGraph) -> list[Any]:
    from moray_scribe.grapher.tracer import trace_call_chains

    chains = trace_call_chains(graph, max_depth=4, chains_per_entry=3, max_chains=4)
    assert chains
    return chains


def test_describer_error_subclass_contract() -> None:
    """The ladder treats every chat failure as DescriberError."""
    assert issubclass(DescriberError, Exception)
