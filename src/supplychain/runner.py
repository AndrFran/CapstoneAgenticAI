"""Turn runner - the API the UI and the tests talk to.

Wraps graph invocation so callers do not have to know about checkpointers,
interrupts or LangSmith config. Two entry points:

* ``run_turn`` - send a user message, get an answer or an approval request.
* ``resume_turn`` - answer an approval request and finish the turn.

Owner: Team Member 4, consumed by Team Member 1's UI.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .config import get_settings
from .graph import build_graph
from .observability import configure_tracing, run_config

_GRAPH = None


def get_compiled_graph():
    """Process-wide compiled graph (its MemorySaver holds conversation memory)."""
    global _GRAPH
    if _GRAPH is None:
        configure_tracing()
        _GRAPH = build_graph()
    return _GRAPH


def reset_graph() -> None:
    """Drop the compiled graph and its memory (tests, and 'new conversation')."""
    global _GRAPH
    _GRAPH = None


@dataclass
class TurnResult:
    """Everything the UI needs to render one turn."""

    thread_id: str
    answer: str | None
    awaiting_approval: bool = False
    approval_request: dict[str, Any] | None = None
    route: list[str] = field(default_factory=list)
    severity: str | None = None
    findings: dict[str, Any] = field(default_factory=dict)
    executed_actions: list[dict[str, Any]] = field(default_factory=list)
    request: dict[str, Any] | None = None
    latency_seconds: float = 0.0
    hops: int = 0

    @property
    def ok(self) -> bool:
        return self.awaiting_approval or bool(self.answer)


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the approval request out of a graph result that was interrupted."""
    interrupts = result.get("__interrupt__") or ()
    for item in interrupts:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            return value
    return None


def _to_turn_result(thread_id: str, result: dict[str, Any], elapsed: float) -> TurnResult:
    approval = _interrupt_payload(result)
    snapshot = get_compiled_graph().get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    state = snapshot.values if snapshot else {}

    return TurnResult(
        thread_id=thread_id,
        answer=state.get("final_response") if not approval else None,
        awaiting_approval=approval is not None,
        approval_request=approval,
        route=list(state.get("visited") or []),
        severity=state.get("severity"),
        findings=dict(state.get("findings") or {}),
        executed_actions=list(state.get("executed_actions") or []),
        request=state.get("request"),
        latency_seconds=round(elapsed, 2),
        hops=state.get("hops", 0),
    )


def run_turn(
    user_message: str,
    thread_id: str | None = None,
    *,
    tags: list[str] | None = None,
) -> TurnResult:
    """Run one conversational turn.

    Args:
        user_message: What the operator typed.
        thread_id: Conversation id. A new one is generated when omitted; pass
            the same id back to continue a conversation.
        tags: Extra LangSmith tags for this run (e.g. an eval-set name).
    """
    thread_id = thread_id or f"thread-{uuid.uuid4().hex[:12]}"
    graph = get_compiled_graph()
    config = run_config(thread_id, tags=tags)

    started = time.perf_counter()
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=user_message)],
            "user_request": user_message,
        },
        config=config,
    )
    return _to_turn_result(thread_id, result, time.perf_counter() - started)


def resume_turn(
    thread_id: str,
    *,
    approved: bool,
    note: str = "",
    tags: list[str] | None = None,
) -> TurnResult:
    """Answer a pending approval request and finish the interrupted turn."""
    graph = get_compiled_graph()
    config = run_config(thread_id, tags=tags)

    started = time.perf_counter()
    result = graph.invoke(
        Command(resume={"approved": approved, "note": note}), config=config
    )
    return _to_turn_result(thread_id, result, time.perf_counter() - started)


def conversation_messages(thread_id: str) -> list[Any]:
    """Full message history for a thread (used to rehydrate the UI)."""
    snapshot = get_compiled_graph().get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot:
        return []
    return list(snapshot.values.get("messages") or [])


def health() -> dict[str, Any]:
    """Startup diagnostics for the UI sidebar and the smoke test."""
    settings = get_settings()
    from .data import access  # local import keeps module import cheap

    try:
        shipments = len(access.load("shipments"))
        data_ok, data_error = True, None
    except Exception as exc:  # noqa: BLE001
        shipments, data_ok, data_error = 0, False, str(exc)

    return {
        "llm_configured": settings.llm_configured,
        "provider": "google_genai",
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "router_reasoning_effort": settings.router_reasoning_effort,
        "data_source": "rest_api" if settings.uses_rest_api else "json_fixtures",
        "data_ok": data_ok,
        "data_error": data_error,
        "shipment_count": shipments,
        "require_approval": settings.require_approval,
        "max_hops": settings.max_hops,
    }
