"""NovaRetail Supply Chain Assistant - Streamlit front end.

Run locally:

    streamlit run streamlit_app.py

Owner: Team Member 1 (Chat UI, conversation memory and history).
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# src-layout: make `supplychain` importable without an editable install, which
# is what Streamlit Community Cloud does when it only runs pip install -r.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supplychain.observability import tracing_status  # noqa: E402
from supplychain.runner import (  # noqa: E402
    TurnResult,
    conversation_turns,
    delete_conversation,
    export_conversation,
    health,
    list_conversations,
    new_thread_id,
    rename_conversation,
    resume_turn,
    run_turn,
)

st.set_page_config(
    page_title="NovaRetail Supply Chain Assistant",
    page_icon="🚚",
    layout="wide",
)

SAMPLE_PROMPTS = [
    "What's the status of shipment SHP-2026-0002?",
    "SHP-2026-0002 is late into WH-N02 - what's the impact and what should we do?",
    "We're short on SKU-1001 at WH-N04. Can we cover it?",
    "SUP-005 has missed two windows. Find alternatives for SKU-3001.",
    "Shipment SHP-2026-0005 arrived damaged at WH-N08. Raise an incident.",
    "Which shipments are delayed right now?",
]

SEVERITY_COLOURS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

ENTITY_LABELS = {
    "shipment_ids": "shipments",
    "supplier_ids": "suppliers",
    "skus": "SKUs",
    "warehouse_ids": "warehouses",
    "order_ids": "orders",
    "incident_ids": "incidents",
    "route_ids": "routes",
}


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def init_state() -> None:
    st.session_state.setdefault("thread_id", new_thread_id())
    st.session_state.setdefault("history", [])  # list of {role, content, meta}
    st.session_state.setdefault("pending", None)  # TurnResult awaiting approval
    st.session_state.setdefault("queued_prompt", None)
    st.session_state.setdefault("renaming", False)


def start_new_conversation() -> None:
    """A new thread. Past conversations stay in history - nothing is wiped."""
    st.session_state.thread_id = new_thread_id()
    st.session_state.history = []
    st.session_state.pending = None
    st.session_state.queued_prompt = None
    st.session_state.renaming = False


def open_conversation(thread_id: str) -> None:
    """Reopen a past conversation, rehydrating it from the checkpointer."""
    st.session_state.thread_id = thread_id
    st.session_state.pending = None
    st.session_state.queued_prompt = None
    st.session_state.renaming = False
    # Restored turns carry their recorded trace metadata, so a reopened
    # conversation shows the route and severity, not just the text.
    st.session_state.history = list(conversation_turns(thread_id))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_intake(request: dict) -> None:
    """Show what intake understood - the TM1 half of the trace."""
    if not request:
        return

    chips = []
    for field, label in ENTITY_LABELS.items():
        values = request.get(field) or []
        if values:
            chips.append(f"**{label}:** {', '.join(values)}")
    if request.get("quantities"):
        chips.append(
            "**quantities:** " + ", ".join(str(q) for q in request["quantities"])
        )
    if chips:
        st.markdown("Understood — " + " · ".join(chips))

    if request.get("resolved_from_memory"):
        carried = ", ".join(
            ENTITY_LABELS.get(f, f) for f in request["resolved_from_memory"]
        )
        st.caption(f"Carried forward from earlier in this conversation: {carried}")

    for unknown in request.get("unknown_identifiers") or []:
        suggestion = (
            f" Closest matches: {', '.join(unknown['suggestions'])}."
            if unknown.get("suggestions")
            else ""
        )
        st.warning(f"`{unknown['value']}` is not in our systems.{suggestion}")

    if request.get("clarification_question"):
        st.info(f"Need to know: {request['clarification_question']}")

    if request.get("extracted_by") == "regex":
        st.caption(
            "Structured extraction was unavailable, so this turn used the "
            "deterministic fallback reading."
        )


def render_trace(meta: dict) -> None:
    """The 'how did it get there' panel under an answer."""
    route = meta.get("route") or []
    with st.expander(
        f"Workflow trace - {len(route)} agent(s), {meta.get('latency', 0)}s",
        expanded=False,
    ):
        cols = st.columns(4)
        cols[0].metric("Agents used", len(route))
        cols[1].metric("Supervisor hops", meta.get("hops", 0))
        cols[2].metric("Latency (s)", meta.get("latency", 0))
        severity = meta.get("severity")
        cols[3].metric(
            "Severity",
            f"{SEVERITY_COLOURS.get(severity, '')} {severity.upper()}" if severity else "-",
        )

        if route:
            st.markdown("**Route:** " + " → ".join(["supervisor", *route, "respond"]))

        request = meta.get("request") or {}
        if request:
            st.markdown("**Intake**")
            st.json(
                {
                    k: v
                    for k, v in request.items()
                    if v not in (None, [], "", False) or k == "is_supported"
                },
                expanded=False,
            )

        entities = meta.get("entities") or {}
        if entities:
            st.markdown("**Conversation memory**")
            st.caption(
                " · ".join(
                    f"{ENTITY_LABELS.get(field, field)}: {', '.join(values)}"
                    for field, values in entities.items()
                )
            )

        for agent, finding in (meta.get("findings") or {}).items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            tool_calls = finding.get("tool_calls") if isinstance(finding, dict) else None
            st.markdown(
                f"**{agent}**"
                + (f" - tools: `{', '.join(tool_calls)}`" if tool_calls else "")
            )
            st.caption(summary)

        for action in meta.get("executed_actions") or []:
            st.success(action.get("result", "action applied"))


def render_history() -> None:
    for entry in st.session_state.history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            meta = entry.get("meta") or {}
            if entry["role"] == "assistant" and meta:
                render_intake(meta.get("request") or {})
                render_trace(meta)


def meta_from(result: TurnResult) -> dict:
    return {
        "route": result.route,
        "severity": result.severity,
        "findings": result.findings,
        "executed_actions": result.executed_actions,
        "request": result.request,
        "entities": result.entities,
        "latency": result.latency_seconds,
        "hops": result.hops,
    }


def handle_result(result: TurnResult) -> None:
    if result.awaiting_approval:
        st.session_state.pending = result
        return

    st.session_state.pending = None
    st.session_state.history.append(
        {
            "role": "assistant",
            "content": result.answer or "_No answer was produced._",
            "meta": meta_from(result),
        }
    )


def render_approval_gate() -> None:
    pending: TurnResult = st.session_state.pending
    request = pending.approval_request or {}

    with st.chat_message("assistant"):
        st.warning("**Approval required before this action is applied**")
        st.markdown(f"**{request.get('description', request.get('action'))}**")
        st.json(request.get("payload", {}), expanded=False)

        note = st.text_input(
            "Note (optional - sent back to the agent on rejection)",
            key=f"note-{pending.thread_id}",
        )
        approve, reject = st.columns(2)
        if approve.button("Approve and apply", type="primary", use_container_width=True):
            with st.spinner("Applying..."):
                handle_result(resume_turn(pending.thread_id, approved=True, note=note))
            st.rerun()
        if reject.button("Reject", use_container_width=True):
            with st.spinner("Cancelling..."):
                handle_result(resume_turn(pending.thread_id, approved=False, note=note))
            st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_status(status: dict) -> None:
    if status["llm_configured"]:
        auth = (
            "Vertex AI" if status["auth_mode"] == "vertex_ai" else "AI Studio key"
        )
        st.success(
            f"Google AI: `{status['model']}` via {auth} "
            f"(reasoning: {status['reasoning_effort']})"
        )
    else:
        st.error(
            "No Google AI credentials. Copy `.env.example` to `.env` and either "
            "set `GOOGLE_API_KEY`, or set `GOOGLE_GENAI_USE_VERTEXAI=true` with "
            "`GOOGLE_CLOUD_PROJECT` and run `gcloud auth application-default "
            "login`. Then restart."
        )

    tracing = tracing_status()
    if tracing["active"]:
        st.success(f"LangSmith tracing → `{tracing['project']}`")
    else:
        st.warning(f"LangSmith tracing off ({tracing['reason']})")

    if status["data_ok"]:
        st.info(
            f"Data source: `{status['data_source']}` "
            f"({status['shipment_count']} shipments)"
        )
    else:
        st.error(f"Data layer error: {status['data_error']}")

    memory_note = (
        "persisted across restarts"
        if status["memory_backend"] == "sqlite"
        else "in-process only"
    )
    st.caption(
        f"Memory: `{status['memory_backend']}` ({memory_note}) · "
        f"{status['conversations_stored']} conversation(s) · "
        f"approval gate {'on' if status['require_approval'] else 'off'} · "
        f"max hops {status['max_hops']}"
    )


def render_conversation_list() -> None:
    st.markdown("**Conversations**")
    conversations = list_conversations(limit=25)
    if not conversations:
        st.caption("No past conversations yet.")
        return

    current = st.session_state.thread_id
    for conversation in conversations:
        is_current = conversation.thread_id == current
        badge = SEVERITY_COLOURS.get(conversation.last_severity or "", "")
        label = f"{badge} {conversation.title}".strip()
        row, delete_col = st.columns([5, 1])
        if row.button(
            label,
            key=f"open-{conversation.thread_id}",
            use_container_width=True,
            type="primary" if is_current else "secondary",
            help=f"{conversation.turns} turn(s) · last activity {conversation.updated_at}",
            disabled=is_current,
        ):
            open_conversation(conversation.thread_id)
            st.rerun()
        if delete_col.button(
            "🗑", key=f"del-{conversation.thread_id}", help="Delete this conversation"
        ):
            delete_conversation(conversation.thread_id)
            if is_current:
                start_new_conversation()
            st.rerun()


def render_sidebar(status: dict) -> None:
    with st.sidebar:
        st.subheader("NovaRetail Ops Assistant")
        st.caption("LangGraph multi-agent supply chain platform")

        render_status(status)
        st.divider()

        if st.button("＋ New conversation", use_container_width=True, type="primary"):
            start_new_conversation()
            st.rerun()

        render_conversation_list()
        st.divider()

        st.markdown("**Try one of these**")
        for index, prompt in enumerate(SAMPLE_PROMPTS):
            if st.button(prompt, key=f"sample-{index}", use_container_width=True):
                st.session_state.queued_prompt = prompt
                st.rerun()

        st.divider()
        st.caption(f"Thread: `{st.session_state.thread_id}`")

        if st.session_state.history:
            if st.session_state.renaming:
                new_title = st.text_input(
                    "Conversation title", key="rename-input"
                )
                if st.button("Save title", use_container_width=True):
                    if new_title.strip():
                        rename_conversation(st.session_state.thread_id, new_title)
                    st.session_state.renaming = False
                    st.rerun()
            elif st.button("Rename this conversation", use_container_width=True):
                st.session_state.renaming = True
                st.rerun()

            st.download_button(
                "Export transcript (.md)",
                data=export_conversation(st.session_state.thread_id),
                file_name=f"{st.session_state.thread_id}.md",
                mime="text/markdown",
                use_container_width=True,
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    init_state()
    status = health()
    render_sidebar(status)

    st.title("Supply Chain Disruption Management")
    st.caption(
        "Ask about shipments, inventory, suppliers or incidents. One assistant, "
        "six agents behind it."
    )

    render_history()

    if st.session_state.pending is not None:
        render_approval_gate()
        return

    # Always render the input box, then let a sidebar sample prompt win.
    typed = st.chat_input("e.g. SHP-2026-0002 is late into WH-N02 - what's the impact?")
    prompt = st.session_state.queued_prompt or typed
    st.session_state.queued_prompt = None
    if not prompt:
        return

    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if not status["llm_configured"]:
        st.session_state.history.append(
            {
                "role": "assistant",
                "content": (
                    "I can't run without Google AI credentials. Add "
                    "`GOOGLE_API_KEY` to `.env` — or configure Vertex AI — and "
                    "restart the app."
                ),
                "meta": {},
            }
        )
        st.rerun()

    with st.chat_message("assistant"), st.spinner("Routing to the right agents..."):
        try:
            result = run_turn(prompt, st.session_state.thread_id)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the UI
            st.session_state.history.append(
                {
                    "role": "assistant",
                    "content": f"Something went wrong: `{exc.__class__.__name__}: {exc}`",
                    "meta": {},
                }
            )
            st.rerun()
        else:
            handle_result(result)
    st.rerun()


if __name__ == "__main__":
    main()
