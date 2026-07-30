"""The LangGraph workflow.

    START -> intake -> supervisor -> {incident_analysis | shipment | inventory
                                      | supplier | recovery} -> supervisor
                                  -> recovery -> approval -> supervisor
                                  -> respond -> END

The Supervisor is the only node that decides where work goes; every worker
returns to it. ``respond`` is the single exit. Two safety properties are
enforced structurally rather than by prompting:

* **Loop guard** - the supervisor counts hops and forces ``respond`` past
  ``SUPPLYCHAIN_MAX_HOPS``, so a confused router cannot spin forever.
* **Human-in-the-loop** - write actions reach ``actions.execute`` only through
  the ``approval`` node, which interrupts for a human decision first.

Owner: Team Member 4 (Recovery & Supervisor Agent Engineer).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import actions
from .agents import analyse_request, decide_route, run_worker, write_response
from .config import get_settings
from .state import SupplyChainState, merge_findings

WORKER_NODES = ("incident_analysis", "shipment", "inventory", "supplier", "recovery")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def intake_node(state: SupplyChainState) -> dict[str, Any]:
    """Request Intake Agent - structure the request, reset per-turn routing."""
    user_request = state.get("user_request") or ""
    # Everything except the message just added is prior conversation, which the
    # intake agent uses to resolve references like "that shipment".
    prior = list(state.get("messages", []))[:-1]

    outcome = analyse_request(
        user_request, prior, state.get("conversation_entities") or {}
    )

    return {
        "request": outcome.as_request(),
        # Entity memory persists across turns - deliberately not reset below.
        "conversation_entities": outcome.entities,
        # Per-turn reset: these are plain fields, not accumulating reducers, so
        # a second question is not answered with the first one's findings.
        "hops": 0,
        "visited": [],
        "findings": {},
        "severity": None,
        "pending_action": None,
        "approval_decision": None,
        "executed_actions": [],
        "final_response": None,
    }


def supervisor_node(state: SupplyChainState) -> dict[str, Any]:
    """Supervisor Agent - choose the next agent, or stop."""
    settings = get_settings()
    hops = state.get("hops", 0) + 1

    if hops > settings.max_hops:
        return {
            "hops": hops,
            "next_agent": "respond",
            "route_reason": (
                f"Hop limit of {settings.max_hops} reached; answering with what "
                "has been gathered."
            ),
            "route_task": "Summarise the findings so far and state what is unresolved.",
        }

    decision, from_llm = decide_route(dict(state))
    return {
        "hops": hops,
        "next_agent": decision.next_agent,
        "route_reason": (
            decision.reason if from_llm else f"{decision.reason} (fallback policy)"
        ),
        "route_task": decision.task,
    }


def _worker_node(name: str):
    """Build the graph node for one worker agent."""

    def node(state: SupplyChainState) -> dict[str, Any]:
        result = run_worker(name, dict(state), state.get("route_task", ""))
        update: dict[str, Any] = {
            "findings": merge_findings(
                state.get("findings"),
                {
                    name: {
                        "summary": result["summary"],
                        "tool_calls": [c["name"] for c in result["tool_calls"]],
                    }
                },
            ),
            "visited": [*(state.get("visited") or []), name],
        }
        if result.get("severity"):
            update["severity"] = result["severity"]
        if result.get("pending_action"):
            update["pending_action"] = result["pending_action"]
        return update

    node.__name__ = f"{name}_node"
    return node


def approval_node(state: SupplyChainState) -> dict[str, Any]:
    """Human-in-the-loop gate for write actions.

    ``interrupt`` suspends the graph and hands the proposal to the caller. The
    UI resumes with ``Command(resume={"approved": bool, ...})``. This node has
    no side effects before the interrupt, so re-entry on resume is safe.
    """
    pending = state.get("pending_action")
    if not pending:
        return {"approval_decision": "not_required"}

    decision = interrupt(
        {
            "type": "approval_request",
            "action": pending.get("action"),
            "description": actions.describe(pending),
            "payload": pending.get("payload", {}),
        }
    )

    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    if not approved:
        note = (
            decision.get("note", "") if isinstance(decision, dict) else ""
        ) or "no reason given"
        return {
            "approval_decision": "rejected",
            "pending_action": None,
            "findings": merge_findings(
                state.get("findings"),
                {
                    "approval": {
                        "summary": (
                            f"Operator rejected: {actions.describe(pending)} ({note}). "
                            "Nothing was changed."
                        )
                    }
                },
            ),
        }

    outcome = actions.execute(pending)
    return {
        "approval_decision": "approved",
        "pending_action": None,
        "executed_actions": [*(state.get("executed_actions") or []), outcome],
        "findings": merge_findings(
            state.get("findings"),
            {"approval": {"summary": f"Operator approved. {outcome.get('result')}"}},
        ),
    }


def respond_node(state: SupplyChainState) -> dict[str, Any]:
    """Compose and record the final answer."""
    answer = write_response(dict(state))
    return {
        "final_response": answer,
        "messages": [AIMessage(content=answer)],
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_from_supervisor(
    state: SupplyChainState,
) -> Literal[
    "incident_analysis", "shipment", "inventory", "supplier", "recovery", "respond"
]:
    target = state.get("next_agent") or "respond"
    if target not in (*WORKER_NODES, "respond"):
        return "respond"
    # Do not re-enter recovery while a proposal is still awaiting approval.
    if target == "recovery" and state.get("pending_action"):
        return "respond"
    return target  # type: ignore[return-value]


def route_from_recovery(state: SupplyChainState) -> Literal["approval", "supervisor"]:
    if state.get("pending_action") and get_settings().require_approval:
        return "approval"
    return "supervisor"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_graph(checkpointer: Any | None = None):
    """Compile the workflow.

    Args:
        checkpointer: LangGraph checkpointer. Defaults to an in-process
            ``MemorySaver``, which provides conversation memory and is what
            makes the approval interrupt resumable. Swap in a persistent
            checkpointer for a real deployment.
    """
    builder = StateGraph(SupplyChainState)

    builder.add_node("intake", intake_node)
    builder.add_node("supervisor", supervisor_node)
    for name in WORKER_NODES:
        builder.add_node(name, _worker_node(name))
    builder.add_node("approval", approval_node)
    builder.add_node("respond", respond_node)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "incident_analysis": "incident_analysis",
            "shipment": "shipment",
            "inventory": "inventory",
            "supplier": "supplier",
            "recovery": "recovery",
            "respond": "respond",
        },
    )

    # Workers report back to the supervisor; recovery may need approval first.
    for name in ("incident_analysis", "shipment", "inventory", "supplier"):
        builder.add_edge(name, "supervisor")
    builder.add_conditional_edges(
        "recovery",
        route_from_recovery,
        {"approval": "approval", "supervisor": "supervisor"},
    )
    builder.add_edge("approval", "supervisor")
    builder.add_edge("respond", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
