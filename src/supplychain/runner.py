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

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from . import memory
from .config import get_settings
from .graph import build_graph
from .observability import configure_tracing, run_config, run_name

_GRAPH = None


def get_compiled_graph():
    """Process-wide compiled graph.

    Its checkpointer is the conversation memory - SQLite by default, so a
    conversation (and a pending approval) survives an app restart.
    """
    global _GRAPH
    if _GRAPH is None:
        configure_tracing()
        _GRAPH = build_graph(memory.get_checkpointer())
    return _GRAPH


def reset_graph() -> None:
    """Drop the compiled graph and rebuild the memory back end.

    Used by tests and when the memory configuration changes. Starting a new
    conversation does **not** need this - just use a new ``thread_id``.
    """
    global _GRAPH
    _GRAPH = None
    memory.reset()


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
    entities: dict[str, list[str]] = field(default_factory=dict)
    latency_seconds: float = 0.0
    hops: int = 0

    @property
    def ok(self) -> bool:
        return self.awaiting_approval or bool(self.answer)

    @property
    def unknown_identifiers(self) -> list[dict[str, Any]]:
        return list((self.request or {}).get("unknown_identifiers") or [])

    @property
    def resolved_from_memory(self) -> list[str]:
        return list((self.request or {}).get("resolved_from_memory") or [])

    @property
    def used_fallback_extraction(self) -> bool:
        return (self.request or {}).get("extracted_by") == "regex"

    def as_meta(self) -> dict[str, Any]:
        """Trace metadata for this turn, persisted with the conversation.

        The checkpointer keeps the messages; this keeps *how* the answer was
        reached, so reopening a past conversation is not lossy.
        """
        return {
            "route": self.route,
            "severity": self.severity,
            "findings": self.findings,
            "executed_actions": self.executed_actions,
            "request": self.request,
            "entities": self.entities,
            "latency": self.latency_seconds,
            "hops": self.hops,
        }


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
        entities=dict(state.get("conversation_entities") or {}),
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
    thread_id = thread_id or new_thread_id()
    graph = get_compiled_graph()
    # The question becomes the run name, so a LangSmith project reads as a list
    # of questions rather than a column of rows all called "LangGraph".
    config = run_config(thread_id, tags=tags, name=run_name(user_message))

    # Register the conversation in the history index. The first user message
    # becomes the title, which is what makes the sidebar list readable.
    store = memory.get_store()
    existing = store.get(thread_id)
    store.upsert(
        thread_id,
        title=None if existing else memory.derive_title(user_message),
    )

    started = time.perf_counter()
    result = graph.invoke(
        {
            "messages": [HumanMessage(content=user_message)],
            "user_request": user_message,
        },
        config=config,
    )
    turn = _to_turn_result(thread_id, result, time.perf_counter() - started)
    if not turn.awaiting_approval:
        store.record_turn(thread_id, severity=turn.severity, meta=turn.as_meta())
    return turn


def resume_turn(
    thread_id: str,
    *,
    approved: bool,
    note: str = "",
    tags: list[str] | None = None,
) -> TurnResult:
    """Answer a pending approval request and finish the interrupted turn."""
    graph = get_compiled_graph()
    config = run_config(
        thread_id,
        tags=tags,
        name=f"{'approved' if approved else 'rejected'} · resume",
    )

    started = time.perf_counter()
    result = graph.invoke(
        Command(resume={"approved": approved, "note": note}), config=config
    )
    return _to_turn_result(thread_id, result, time.perf_counter() - started)


def new_thread_id() -> str:
    """Identifier for a fresh conversation."""
    return f"thread-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Conversation memory and history
# ---------------------------------------------------------------------------


def conversation_messages(thread_id: str) -> list[Any]:
    """Full message history for a thread (used to rehydrate the UI)."""
    snapshot = get_compiled_graph().get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot:
        return []
    return list(snapshot.values.get("messages") or [])


def conversation_turns(thread_id: str) -> list[dict[str, Any]]:
    """Message history as ``{role, content, meta}``, ready to render.

    Assistant turns are paired with the trace metadata recorded when they ran,
    so reopening a conversation restores the route, severity and intake summary
    rather than just the text.
    """
    metas = memory.get_store().turn_meta(thread_id)
    turns: list[dict[str, Any]] = []
    assistant_index = 0

    for message in conversation_messages(thread_id):
        role = "assistant" if isinstance(message, AIMessage) else "user"
        content = message.content
        if not isinstance(content, str):
            content = "\n".join(
                block.get("text", "")
                for block in (content or [])
                if isinstance(block, dict)
            )
        if not content or not content.strip():
            continue

        entry: dict[str, Any] = {"role": role, "content": content.strip(), "meta": {}}
        if role == "assistant":
            if assistant_index < len(metas):
                entry["meta"] = metas[assistant_index]
            assistant_index += 1
        turns.append(entry)
    return turns


def conversation_entities(thread_id: str) -> dict[str, list[str]]:
    """The entity memory accumulated by a conversation."""
    snapshot = get_compiled_graph().get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot:
        return {}
    return dict(snapshot.values.get("conversation_entities") or {})


def list_conversations(limit: int = 50) -> list[memory.Conversation]:
    """Past conversations, most recently updated first."""
    return memory.get_store().list(limit=limit)


def get_conversation(thread_id: str) -> memory.Conversation | None:
    return memory.get_store().get(thread_id)


def rename_conversation(thread_id: str, title: str) -> None:
    memory.get_store().rename(thread_id, title)


def delete_conversation(thread_id: str) -> None:
    """Remove a conversation from history and drop its checkpoints."""
    memory.get_store().delete(thread_id)


def export_conversation(thread_id: str) -> str:
    """Render a conversation as markdown, for the LangSmith report or a demo."""
    record = get_conversation(thread_id)
    lines = [
        f"# {record.title if record else thread_id}",
        "",
        f"- Thread: `{thread_id}`",
    ]
    if record:
        lines += [
            f"- Started: {record.created_at}",
            f"- Last activity: {record.updated_at}",
            f"- Turns: {record.turns}",
        ]
        if record.last_severity:
            lines.append(f"- Last assessed severity: {record.last_severity}")
    entities = conversation_entities(thread_id)
    if entities:
        lines.append("- Entities in memory: " + ", ".join(
            f"{field}={'/'.join(values)}" for field, values in entities.items()
        ))
    lines.append("")

    for turn in conversation_turns(thread_id):
        speaker = "Operator" if turn["role"] == "user" else "Assistant"
        lines += [f"**{speaker}**", "", turn["content"], ""]
    return "\n".join(lines)


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
        # Two credential paths: an AI Studio API key, or the Vertex AI backend
        # with gcloud ADC. Worth surfacing - a demo that silently used the wrong
        # one is hard to debug from the UI.
        "auth_mode": "vertex_ai" if settings.use_vertexai else "api_key",
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "router_reasoning_effort": settings.router_reasoning_effort,
        "memory_backend": memory.backend_name(),
        "conversations_stored": len(memory.get_store().list(limit=1000)),
        "data_source": "rest_api" if settings.uses_rest_api else "json_fixtures",
        "data_ok": data_ok,
        "data_error": data_error,
        "shipment_count": shipments,
        "require_approval": settings.require_approval,
        "max_hops": settings.max_hops,
    }
