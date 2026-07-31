"""Workflow tests.

These run without an API key: the graph compiles lazily, so its structure,
routing policy, loop guard, approval gate and action execution can all be
tested without calling a model.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from supplychain import actions
from supplychain.agents import supervisor
from supplychain.agents.base import AGENT_TOOLS, find_pending_proposal
from supplychain.agents.intake import _regex_intake
from supplychain.data import access
from supplychain.graph import (
    WORKER_NODES,
    build_graph,
    route_from_recovery,
    route_from_supervisor,
)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_graph_compiles_without_an_api_key():
    graph = build_graph(checkpointer=MemorySaver())
    nodes = set(graph.get_graph().nodes)
    for expected in ("intake", "supervisor", "approval", "respond", *WORKER_NODES):
        assert expected in nodes


def test_every_worker_node_has_tools():
    for name in WORKER_NODES:
        assert AGENT_TOOLS[name], f"{name} has no tools"


def test_only_recovery_can_propose_writes():
    proposal_names = {"propose_incident", "propose_reroute", "propose_escalation"}
    for name, tools in AGENT_TOOLS.items():
        names = {tool.name for tool in tools}
        overlap = names & proposal_names
        if name == "recovery":
            assert overlap == proposal_names
        else:
            assert not overlap, f"{name} must not be able to propose writes"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_supervisor_route_falls_back_to_respond_on_a_bad_target():
    assert route_from_supervisor({"next_agent": "marketing"}) == "respond"
    assert route_from_supervisor({}) == "respond"


def test_supervisor_will_not_re_enter_recovery_while_approval_is_pending():
    state = {"next_agent": "recovery", "pending_action": {"action": "create_incident"}}
    assert route_from_supervisor(state) == "respond"


def _status_query_state(**overrides):
    state = {
        "request": {"incident_type": "status_query", "is_supported": True},
        "visited": [],
    }
    state.update(overrides)
    return state


def test_status_query_never_routes_to_recovery():
    """A read-only lookup must not end at an approval gate."""
    from supplychain.graph import _constrain_read_only

    target, reason = _constrain_read_only(_status_query_state(), "recovery")
    assert target == "respond"
    assert "read-only" in reason


def test_status_query_stops_after_the_worker_budget():
    from supplychain.graph import STATUS_QUERY_WORKER_BUDGET, _constrain_read_only

    visited = ["shipment", "inventory"][:STATUS_QUERY_WORKER_BUDGET]
    target, reason = _constrain_read_only(
        _status_query_state(visited=visited), "supplier"
    )
    assert target == "respond"
    assert "stopping rather than gathering more" in reason


def test_status_query_allows_the_first_specialist():
    from supplychain.graph import _constrain_read_only

    target, reason = _constrain_read_only(_status_query_state(), "shipment")
    assert target == "shipment"
    assert reason is None


def test_status_query_allows_a_second_agent_when_the_question_spans_two():
    """"Status of X, and do we have stock?" legitimately needs two specialists."""
    from supplychain.graph import _constrain_read_only

    target, reason = _constrain_read_only(
        _status_query_state(visited=["shipment"]), "inventory"
    )
    assert target == "inventory"
    assert reason is None


def test_the_clamp_only_applies_to_status_queries():
    from supplychain.graph import _constrain_read_only

    for incident_type in ("shipment_delay", "supplier_failure", "damaged_goods"):
        state = {
            "request": {"incident_type": incident_type},
            "visited": ["incident_analysis", "shipment", "inventory"],
        }
        target, reason = _constrain_read_only(state, "recovery")
        assert target == "recovery", f"{incident_type} must still reach recovery"
        assert reason is None


def test_the_clamp_handles_a_missing_request():
    from supplychain.graph import _constrain_read_only

    target, reason = _constrain_read_only({}, "recovery")
    assert (target, reason) == ("recovery", None)


# ---------------------------------------------------------------------------
# Cross-agent error handling
# ---------------------------------------------------------------------------


def _failed_state(agent: str = "shipment", **overrides):
    state = {
        "request": {"incident_type": "shipment_delay", "is_supported": True},
        "visited": [agent],
        "findings": {
            agent: {"summary": "could not be completed", "failed": True},
            "inventory": {"summary": "3 warehouses can cover it"},
        },
    }
    state.update(overrides)
    return state


def test_a_dead_worker_becomes_a_finding_instead_of_an_exception(monkeypatch):
    """One agent failing must not cost the work the others already did."""
    from supplychain import graph

    monkeypatch.setattr(
        graph,
        "run_worker",
        lambda *_, **__: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED")),
    )
    node = graph._worker_node("shipment")

    update = node({"findings": {"inventory": {"summary": "earlier work"}}, "visited": ["inventory"]})

    finding = update["findings"]["shipment"]
    assert finding["failed"] is True
    assert "rate limit" in finding["summary"]
    # The earlier agent's work survives, and the dead one is not tried again.
    assert update["findings"]["inventory"]["summary"] == "earlier work"
    assert update["visited"] == ["inventory", "shipment"]


def test_a_dead_worker_reports_the_cause_without_a_stack_trace():
    from supplychain.graph import _worker_failure

    update = _worker_failure("supplier", {}, RuntimeError("503 UNAVAILABLE"))
    summary = update["findings"]["supplier"]["summary"]

    assert "temporarily unavailable" in summary
    assert "Traceback" not in summary and "503" not in summary
    assert update["findings"]["supplier"]["error"] == "RuntimeError"


def test_a_healthy_worker_is_unaffected(monkeypatch):
    from supplychain import graph

    monkeypatch.setattr(
        graph,
        "run_worker",
        lambda *_, **__: {
            "summary": "tracked it",
            "tool_calls": [{"name": "track_shipment"}],
            "severity": "high",
            "pending_action": None,
        },
    )
    update = graph._worker_node("shipment")({})

    assert update["findings"]["shipment"]["tool_calls"] == ["track_shipment"]
    assert "failed" not in update["findings"]["shipment"]
    assert update["severity"] == "high"


def test_the_responder_is_told_a_check_failed():
    """Found by the end-to-end system test.

    Given only the failure summary as prose, the model answered "I am
    attempting to re-query the database" - nothing runs after the responder,
    so that is a promise the system cannot keep. The brief now marks it.
    """
    from supplychain.agents.responder import _brief

    brief = _brief(
        {
            "user_request": "status of SHP-2026-0002?",
            "findings": {
                "shipment": {"summary": "could not be completed", "failed": True},
                "inventory": {"summary": "3 warehouses can cover it"},
            },
        }
    )
    assert "COULD NOT COMPLETE" in brief
    assert "do not retry" in brief
    # The healthy agent is not tarred with the same brush.
    assert brief.count("COULD NOT COMPLETE") == 1
    assert "3 warehouses can cover it" in brief


def test_the_responder_brief_is_unmarked_when_nothing_failed():
    from supplychain.agents.responder import _brief

    brief = _brief({"user_request": "x", "findings": {"shipment": {"summary": "fine"}}})
    assert "COULD NOT COMPLETE" not in brief


def test_failed_agents_are_listed():
    from supplychain.graph import failed_agents

    assert failed_agents(_failed_state()) == ["shipment"]
    assert failed_agents({"findings": {"shipment": {"summary": "fine"}}}) == []
    assert failed_agents({}) == []


def test_the_router_may_not_re_dispatch_a_failed_agent():
    """The LLM sees the failure summary and may read it as "try again"."""
    from supplychain.graph import _apply_routing_policy

    target, reason = _apply_routing_policy(_failed_state(), "shipment")
    assert target == "respond"
    assert "failed this turn" in reason


def test_a_failed_agent_does_not_block_the_others():
    from supplychain.graph import _apply_routing_policy

    target, reason = _apply_routing_policy(_failed_state(), "recovery")
    assert (target, reason) == ("recovery", None)


def test_the_failed_clamp_outranks_the_read_only_clamp():
    """Both would fire; the failure reason is the one the operator needs."""
    from supplychain.graph import _apply_routing_policy

    state = _failed_state(
        agent="shipment", request={"incident_type": "status_query", "is_supported": True}
    )
    target, reason = _apply_routing_policy(state, "shipment")
    assert target == "respond"
    assert "failed this turn" in reason


def test_the_routing_policy_is_a_no_op_when_nothing_is_wrong():
    from supplychain.graph import _apply_routing_policy

    state = {"request": {"incident_type": "shipment_delay"}, "visited": [], "findings": {}}
    assert _apply_routing_policy(state, "incident_analysis") == ("incident_analysis", None)


def test_recovery_routes_to_approval_only_with_a_pending_action():
    assert route_from_recovery({"pending_action": None}) == "supervisor"
    assert (
        route_from_recovery({"pending_action": {"action": "create_incident"}})
        == "approval"
    )


def test_fallback_policy_starts_with_incident_analysis_for_a_disruption():
    decision = supervisor._fallback_decision(
        {"request": {"incident_type": "shipment_delay", "is_supported": True}, "visited": []}
    )
    assert decision.next_agent == "incident_analysis"


def test_fallback_policy_sends_a_status_query_straight_to_the_specialist():
    decision = supervisor._fallback_decision(
        {"request": {"incident_type": "status_query", "is_supported": True}, "visited": []}
    )
    assert decision.next_agent == "shipment"


def test_fallback_policy_short_circuits_unsupported_requests():
    decision = supervisor._fallback_decision(
        {"request": {"incident_type": "other", "is_supported": False}, "visited": []}
    )
    assert decision.next_agent == "respond"


def test_fallback_policy_never_revisits_an_agent():
    decision = supervisor._fallback_decision(
        {
            "request": {"incident_type": "inventory_shortage", "is_supported": True},
            "visited": ["inventory", "supplier", "recovery"],
        }
    )
    assert decision.next_agent == "respond"


def test_fallback_policy_terminates_from_every_state():
    """Walk the deterministic policy to exhaustion for each incident type."""
    for incident_type in (
        "shipment_delay",
        "supplier_failure",
        "inventory_shortage",
        "route_disruption",
        "damaged_goods",
        "status_query",
        "other",
    ):
        visited: list[str] = []
        for _ in range(10):
            decision = supervisor._fallback_decision(
                {
                    "request": {"incident_type": incident_type, "is_supported": True},
                    "visited": list(visited),
                }
            )
            if decision.next_agent == "respond":
                break
            visited.append(decision.next_agent)
        else:
            raise AssertionError(f"{incident_type} never reached respond: {visited}")


# ---------------------------------------------------------------------------
# Intake fallback
# ---------------------------------------------------------------------------


def test_regex_intake_extracts_every_identifier_type():
    result = _regex_intake(
        "SHP-2026-0002 from SUP-005 is late into WH-N02 with SKU-3001, "
        "affecting ORD-2026-00007 and incident INC-2026-0001"
    )
    assert result.shipment_ids == ["SHP-2026-0002"]
    assert result.supplier_ids == ["SUP-005"]
    assert result.warehouse_ids == ["WH-N02"]
    assert result.skus == ["SKU-3001"]
    assert result.order_ids == ["ORD-2026-00007"]
    assert result.incident_ids == ["INC-2026-0001"]


def test_regex_intake_classifies_by_keyword():
    assert _regex_intake("this arrived damaged").incident_type == "damaged_goods"
    assert _regex_intake("we are out of stock").incident_type == "inventory_shortage"
    assert _regex_intake("where is my shipment").incident_type == "status_query"


def test_regex_intake_is_case_insensitive():
    result = _regex_intake("shp-2026-0002 into wh-n02")
    assert result.shipment_ids == ["SHP-2026-0002"]
    assert result.warehouse_ids == ["WH-N02"]


# ---------------------------------------------------------------------------
# Proposal extraction and execution
# ---------------------------------------------------------------------------


def test_find_pending_proposal_reads_the_last_proposal():
    from langchain_core.messages import AIMessage

    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "generate_recovery_plan", "args": {}, "id": "1"},
                {
                    "name": "propose_incident",
                    "args": {"type": "shipment_delay", "title": "t"},
                    "id": "2",
                },
            ],
        )
    ]
    proposal = find_pending_proposal(messages)
    assert proposal["action"] == "create_incident"
    assert proposal["payload"]["title"] == "t"
    assert proposal["proposed_by"] == "recovery"


def test_find_pending_proposal_returns_none_without_one():
    from langchain_core.messages import AIMessage

    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "generate_recovery_plan", "args": {}, "id": "1"}],
        )
    ]
    assert find_pending_proposal(messages) is None


def test_execute_create_incident_writes_a_record():
    before = len(access.load("incidents"))
    outcome = actions.execute(
        {
            "action": "create_incident",
            "payload": {
                "type": "shipment_delay",
                "severity": "high",
                "title": "SHP-2026-0002 delayed",
                "description": "test",
                "related_shipment_id": "SHP-2026-0002",
            },
        }
    )
    assert outcome["ok"] is True
    assert len(access.load("incidents")) == before + 1
    assert access.get_incident(outcome["incident_id"])["severity"] == "high"


def test_execute_reroute_updates_the_shipment():
    outcome = actions.execute(
        {
            "action": "reroute_shipment",
            "payload": {
                "shipment_id": "SHP-2026-0002",
                "from_route_id": "RTE-105",
                "to_route_id": "RTE-111",
                "reason": "bridge closure",
            },
        }
    )
    assert outcome["ok"] is True
    shipment = access.get_shipment("SHP-2026-0002")
    assert shipment["route_id"] == "RTE-111"
    assert shipment["rerouted_from"] == "RTE-105"


def test_execute_escalation_raises_severity():
    outcome = actions.execute(
        {
            "action": "escalate_incident",
            "payload": {
                "incident_id": "INC-2026-0001",
                "reason": "no recovery path",
                "escalate_to": "COO",
            },
        }
    )
    assert outcome["ok"] is True
    incident = access.get_incident("INC-2026-0001")
    assert incident["escalated"] is True
    assert incident["severity"] == "critical"


def test_execute_fails_safely_on_unknown_ids():
    for action in (
        {"action": "reroute_shipment", "payload": {"shipment_id": "SHP-0", "to_route_id": "RTE-1"}},
        {"action": "escalate_incident", "payload": {"incident_id": "INC-0"}},
        {"action": "delete_everything", "payload": {}},
    ):
        outcome = actions.execute(action)
        assert outcome["ok"] is False
        assert "nothing changed" in outcome["result"] or "unknown action" in outcome["result"]


def test_describe_is_human_readable():
    text = actions.describe(
        {
            "action": "reroute_shipment",
            "payload": {
                "shipment_id": "SHP-2026-0002",
                "from_route_id": "RTE-105",
                "to_route_id": "RTE-108",
                "reason": "bridge closure",
            },
        }
    )
    assert "SHP-2026-0002" in text and "RTE-108" in text
