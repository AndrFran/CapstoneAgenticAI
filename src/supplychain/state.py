"""Shared workflow state.

Every node reads and writes this one TypedDict. Keeping the contract in a
single file is what lets four people build agents in parallel without stepping
on each other.

Owner: Team Member 4 (shared state), consumed by everyone.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

# The complete set of routing targets. `RouteDecision.next_agent` below is the
# enforced contract; `graph.WORKER_NODES` is this set minus `respond`.
AGENT_NAMES: tuple[str, ...] = (
    "incident_analysis",
    "shipment",
    "inventory",
    "supplier",
    "recovery",
    "respond",
)


def merge_findings(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge two findings maps (last write per agent wins).

    Called explicitly by the nodes rather than registered as a state reducer: a
    reducer would also merge across conversational turns, so turn 2 would answer
    with turn 1's findings still attached. Nodes run sequentially here, so an
    explicit merge is safe and lets ``intake`` reset per turn.
    """
    return {**(left or {}), **(right or {})}


class IntakeResult(BaseModel):
    """Structured output of the Request Intake Agent."""

    incident_type: Literal[
        "shipment_delay",
        "supplier_failure",
        "inventory_shortage",
        "route_disruption",
        "damaged_goods",
        "status_query",
        "other",
    ] = Field(description="The kind of supply chain request or incident.")
    intent: str = Field(description="One sentence describing what the user wants.")
    shipment_ids: list[str] = Field(default_factory=list)
    supplier_ids: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)
    warehouse_ids: list[str] = Field(default_factory=list)
    order_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    route_ids: list[str] = Field(default_factory=list)
    quantities: list[int] = Field(default_factory=list)
    missing_information: list[str] = Field(
        default_factory=list,
        description="Facts that are required to act but were not supplied.",
    )
    is_supported: bool = Field(
        default=True,
        description="False when the request is outside supply chain operations.",
    )
    clarification_question: str | None = Field(
        default=None,
        description="Question to ask the user when critical information is missing.",
    )


class RouteDecision(BaseModel):
    """Structured output of the Supervisor Agent."""

    next_agent: Literal[
        "incident_analysis",
        "shipment",
        "inventory",
        "supplier",
        "recovery",
        "respond",
    ] = Field(description="Which agent should handle the request next.")
    reason: str = Field(description="Why this agent, in one sentence.")
    task: str = Field(
        description="The specific question or task to hand to that agent."
    )


class PendingAction(TypedDict, total=False):
    """A write action proposed by the Recovery Agent, awaiting approval."""

    action: str  # create_incident | reroute_shipment | escalate_incident
    payload: dict[str, Any]
    proposed_by: str
    tool_call_id: str


class SupplyChainState(TypedDict, total=False):
    """The graph's shared state."""

    # Conversation
    messages: Annotated[list[AnyMessage], add_messages]

    # Intake
    request: dict[str, Any] | None  # serialised IntakeResult + intake metadata
    user_request: str

    # Conversation-level entity memory: the identifiers this conversation has
    # mentioned, most recent first, per kind. Deliberately NOT reset per turn -
    # it is what lets "that shipment" resolve on a later turn.
    conversation_entities: dict[str, list[str]]

    # Routing. `hops` and `visited` are last-write-wins (not reducers) so the
    # intake node can reset them at the start of every conversational turn -
    # an accumulating reducer would exhaust the loop guard after a few turns.
    next_agent: str
    route_reason: str
    route_task: str
    hops: int
    visited: list[str]

    # Agent outputs, keyed by agent name. Merged explicitly by the nodes (see
    # merge_findings) so they can be reset at the start of each turn.
    findings: dict[str, Any]
    severity: str | None

    # Human-in-the-loop
    pending_action: PendingAction | None
    approval_decision: str | None  # approved | rejected | not_required
    executed_actions: list[dict[str, Any]]

    # Output
    final_response: str | None


# Callers do not build a full state: `runner.run_turn` passes only `messages`
# and `user_request`, and `graph.intake_node` initialises the rest. Everything
# else comes from the checkpointer.
