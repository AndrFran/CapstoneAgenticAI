"""NovaRetail Supply Chain Assistant - Streamlit front end.

Run locally:

    streamlit run streamlit_app.py

Owner: Team Member 1 (Chat UI, conversation memory and history).
"""

from __future__ import annotations

import sys
import uuid
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
    health,
    reset_graph,
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


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def init_state() -> None:
    st.session_state.setdefault("thread_id", f"thread-{uuid.uuid4().hex[:12]}")
    st.session_state.setdefault("history", [])  # list of {role, content, meta}
    st.session_state.setdefault("pending", None)  # TurnResult awaiting approval
    st.session_state.setdefault("queued_prompt", None)


def new_conversation() -> None:
    reset_graph()
    st.session_state.thread_id = f"thread-{uuid.uuid4().hex[:12]}"
    st.session_state.history = []
    st.session_state.pending = None
    st.session_state.queued_prompt = None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_trace(meta: dict) -> None:
    """The 'how did it get there' panel under an answer."""
    route = meta.get("route") or []
    with st.expander(
        f"Workflow trace - {len(route)} agent(s), {meta.get('latency', 0)}s", expanded=False
    ):
        cols = st.columns(4)
        cols[0].metric("Agents used", len(route))
        cols[1].metric("Supervisor hops", meta.get("hops", 0))
        cols[2].metric("Latency (s)", meta.get("latency", 0))
        cols[3].metric("Severity", (meta.get("severity") or "-").upper())

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

        for agent, finding in (meta.get("findings") or {}).items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            tool_calls = finding.get("tool_calls") if isinstance(finding, dict) else None
            st.markdown(f"**{agent}**" + (f" - tools: `{', '.join(tool_calls)}`" if tool_calls else ""))
            st.caption(summary)

        for action in meta.get("executed_actions") or []:
            st.success(action.get("result", "action applied"))


def render_history() -> None:
    for entry in st.session_state.history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            if entry["role"] == "assistant" and entry.get("meta"):
                render_trace(entry["meta"])


def meta_from(result: TurnResult) -> dict:
    return {
        "route": result.route,
        "severity": result.severity,
        "findings": result.findings,
        "executed_actions": result.executed_actions,
        "request": result.request,
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


def render_sidebar() -> None:
    with st.sidebar:
        st.subheader("NovaRetail Ops Assistant")
        st.caption("LangGraph multi-agent supply chain platform")

        status = health()
        if status["llm_configured"]:
            st.success(
                f"Google AI: `{status['model']}` "
                f"(reasoning: {status['reasoning_effort']})"
            )
        else:
            st.error(
                "GOOGLE_API_KEY is not set. Copy `.env.example` to `.env` and "
                "add a Google AI Studio key, then restart."
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

        st.caption(
            f"Approval gate: {'on' if status['require_approval'] else 'off'} · "
            f"max hops: {status['max_hops']}"
        )

        st.divider()
        st.markdown("**Try one of these**")
        for index, prompt in enumerate(SAMPLE_PROMPTS):
            if st.button(prompt, key=f"sample-{index}", use_container_width=True):
                st.session_state.queued_prompt = prompt
                st.rerun()

        st.divider()
        if st.button("New conversation", use_container_width=True):
            new_conversation()
            st.rerun()
        st.caption(f"Thread: `{st.session_state.thread_id}`")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    init_state()
    render_sidebar()

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

    if not health()["llm_configured"]:
        st.session_state.history.append(
            {
                "role": "assistant",
                "content": (
                    "I can't run without a Google AI API key. Add "
                    "`GOOGLE_API_KEY` to `.env` and restart the app."
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
