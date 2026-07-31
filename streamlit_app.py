"""NovaRetail Supply Chain Assistant - Streamlit front end.

Run locally:

    streamlit run streamlit_app.py

Owner: Team Member 1 (Chat UI, conversation memory and history).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# src-layout: make `supplychain` importable without an editable install, which
# is what Streamlit Community Cloud does when it only runs pip install -r.
SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supplychain.observability import tracing_status  # noqa: E402
from supplychain.progress import PIPELINE, TurnProgress  # noqa: E402
from supplychain.runner import (  # noqa: E402
    TurnResult,
    conversation_turns,
    delete_conversation,
    export_conversation,
    health,
    list_conversations,
    new_thread_id,
    pending_approval,
    rename_conversation,
    resume_turn,
    stream_turn,
)
from supplychain.ui import theme, visuals  # noqa: E402
from supplychain.ui.overview import Snapshot, fleet_snapshot  # noqa: E402

# A local file, not `:material/...:` - Streamlit resolves a material page_icon
# to a fonts.gstatic.com URL, so the tab icon would fail with no network and
# would ping Google on every load. Falls back to an emoji if the file is
# missing, because a bad page_icon aborts the whole script.
FAVICON = Path(__file__).resolve().parent / "assets" / "favicon.svg"

st.set_page_config(
    page_title="NovaRetail Ops Assistant",
    page_icon=str(FAVICON) if FAVICON.exists() else "🚚",
    layout="wide",
)

# The zero state. Each is a real question against the fixtures, and between
# them they exercise every panel and both the approval gate and entity memory.
SAMPLE_PROMPTS = [
    "What's the status of shipment SHP-2026-0002?",
    "SHP-2026-0002 is late into WH-N02 - what's the impact and what should we do?",
    "We're short on SKU-1001 at WH-N04. Can we cover it?",
    "SUP-005 has missed two windows. Find alternatives for SKU-3001.",
    "Shipment SHP-2026-0005 arrived damaged at WH-N08. Raise an incident.",
    "Which shipments are delayed right now?",
]

ENTITY_LABELS = theme.ENTITY_LABELS


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
    st.session_state.queued_prompt = None
    st.session_state.renaming = False
    # Restored turns carry their recorded trace metadata, so a reopened
    # conversation shows the route and severity, not just the text.
    st.session_state.history = list(conversation_turns(thread_id))
    # If this conversation was left parked on an approval, the graph is still
    # parked on it. Rebuild the gate from the checkpoint rather than dropping
    # the turn on the floor with no way to approve or reject it.
    st.session_state.pending = pending_approval(thread_id)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_intake(request: dict) -> None:
    """Show what intake understood - the TM1 half of the trace."""
    if not request:
        return

    identifiers = [
        (field, value)
        for field in ENTITY_LABELS
        for value in (request.get(field) or [])
    ]
    if identifiers:
        st.html(theme.chips(identifiers))
    if request.get("quantities"):
        st.caption(
            "Quantities read: " + ", ".join(str(q) for q in request["quantities"])
        )

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


def render_shipment_lane(panel: visuals.ShipmentTimeline) -> None:
    theme.panel_title("Shipment lane", "local_shipping")
    st.html(theme.shipment_lane(panel))


def render_stock_positions(panel: visuals.StockPositions) -> None:
    theme.panel_title(f"Stock cover — {panel.sku}", "inventory_2")
    frame = pd.DataFrame(panel.rows)
    st.bar_chart(
        frame.set_index("warehouse_id")[["available", "reorder_point"]],
        color=["#38BDF8", "#FB923C"],
        stack=False,
        height=240,
    )
    st.caption(
        f"{panel.product_name or panel.sku} · available stock against each "
        "warehouse's reorder point"
        + (
            f" · {panel.highlight_warehouse} is the site in question"
            if panel.highlight_warehouse
            else ""
        )
    )
    st.dataframe(
        frame[
            [
                "warehouse_id",
                "warehouse_name",
                "available",
                "reorder_point",
                "days_of_cover",
                "below_reorder_point",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "warehouse_id": "Warehouse",
            "warehouse_name": "Name",
            "available": st.column_config.NumberColumn("Available", format="%d"),
            "reorder_point": st.column_config.NumberColumn("Reorder pt", format="%d"),
            "days_of_cover": st.column_config.NumberColumn("Cover (days)", format="%.1f"),
            "below_reorder_point": st.column_config.CheckboxColumn("Short"),
        },
    )


def render_warehouse_stock(panel: visuals.WarehouseStock) -> None:
    theme.panel_title(
        f"Lowest cover — {panel.warehouse_name or panel.warehouse_id}", "warehouse"
    )
    frame = pd.DataFrame(panel.rows)
    st.bar_chart(
        frame.set_index("sku")[["days_of_cover"]],
        color=["#38BDF8"],
        height=240,
    )
    st.caption(
        f"{panel.short_count} line(s) below reorder point at {panel.warehouse_id}; "
        "worst cover first"
    )


def render_delay_backlog(panel: visuals.DelayBacklog) -> None:
    theme.panel_title("Delay backlog by destination", "schedule")
    frame = pd.DataFrame(panel.rows)
    st.bar_chart(
        frame.set_index("warehouse_name")[["shipments"]],
        color=["#FB923C"],
        height=240,
    )
    st.caption(
        f"{panel.total} shipments running late, grouped by the warehouse waiting "
        "on them"
    )


def render_supplier_options(panel: visuals.SupplierOptions) -> None:
    theme.panel_title(f"Supplier options — {panel.sku}", "factory")
    frame = pd.DataFrame(panel.rows)
    st.dataframe(
        frame[
            [
                "supplier_id",
                "name",
                "country",
                "unit_price",
                "lead_time_days",
                "on_time_delivery_rate",
                "tier",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "supplier_id": "Supplier",
            "name": "Name",
            "country": "Country",
            "unit_price": st.column_config.NumberColumn("Unit price", format="$%.2f"),
            "lead_time_days": st.column_config.NumberColumn("Lead time", format="%d d"),
            "on_time_delivery_rate": st.column_config.ProgressColumn(
                "On time", format="%.0f%%", min_value=0, max_value=1
            ),
            "tier": "Tier",
        },
    )
    if panel.exclude_supplier_id:
        st.caption(f"Cheapest first. {panel.exclude_supplier_id} is the incumbent.")


RENDERERS = {
    visuals.ShipmentTimeline: render_shipment_lane,
    visuals.StockPositions: render_stock_positions,
    visuals.WarehouseStock: render_warehouse_stock,
    visuals.DelayBacklog: render_delay_backlog,
    visuals.SupplierOptions: render_supplier_options,
}


def render_visuals(meta: dict) -> None:
    """Draw the numbers behind an answer, under the answer.

    Never let a broken panel take the answer down with it - the prose is the
    deliverable, the chart is support.
    """
    try:
        panels = visuals.build_panels(meta)
    except Exception:  # noqa: BLE001
        return
    for panel in panels:
        renderer = RENDERERS.get(type(panel))
        if renderer is None:
            continue
        try:
            renderer(panel)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"(could not draw {type(panel).__name__}: {exc})")


def render_trace(meta: dict) -> None:
    """The 'how did it get there' panel under an answer."""
    route = meta.get("route") or []
    severity = meta.get("severity")
    agents = f"{len(route)} agent" + ("" if len(route) == 1 else "s")
    with st.expander(
        f"Workflow trace · {agents} · {meta.get('latency', 0)}s", expanded=False
    ):
        st.html(theme.route_chain(route))
        st.html(
            theme.stats(
                [
                    ("Agents used", len(route)),
                    ("Supervisor hops", meta.get("hops", 0)),
                    ("Latency", f"{meta.get('latency', 0)}s"),
                    ("Severity", severity.upper() if severity else "—"),
                ]
            )
        )

        request = meta.get("request") or {}
        if request:
            theme.panel_title("What intake understood", "inbox")
            st.html(
                theme.kv_grid(
                    {
                        key: value
                        for key, value in request.items()
                        if value not in (None, [], "", False)
                    }
                )
            )

        entities = meta.get("entities") or {}
        if entities:
            theme.panel_title("Conversation memory", "memory")
            st.html(
                theme.kv_grid(
                    {
                        ENTITY_LABELS.get(field, field): values
                        for field, values in entities.items()
                    }
                )
            )

        findings = meta.get("findings") or {}
        if findings:
            theme.panel_title("What each agent found", "checklist")
        for agent, finding in findings.items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            tool_calls = finding.get("tool_calls") if isinstance(finding, dict) else None
            glyph, label = theme.AGENT_META.get(agent, ("label", agent))
            st.html(
                f'<div class="nr-chain" style="margin-bottom:0.25rem">'
                f'<span class="nr-chain__step nr-chain__step--edge">'
                f"{theme.icon(glyph)}{label}</span>"
                + (
                    f'<span class="nr-chip__kind">{", ".join(tool_calls)}</span>'
                    if tool_calls
                    else ""
                )
                + "</div>"
            )
            st.caption(summary)

        for action in meta.get("executed_actions") or []:
            st.success(
                action.get("result", "action applied"),
                icon=":material/check_circle:",
            )


def render_history() -> None:
    for entry in st.session_state.history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            meta = entry.get("meta") or {}
            if entry["role"] == "assistant" and meta:
                render_intake(meta.get("request") or {})
                render_visuals(meta)
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

    with st.chat_message("assistant"), st.container(key="nr-gate"):
        st.html(
            f'<div class="nr-gate-title">{theme.icon("gpp_maybe")}Approval required'
            "</div>"
            '<div class="nr-gate-note">Nothing is written until you decide. The '
            "recovery agent can only describe this action — it has no write "
            "tools of its own.</div>"
        )
        st.markdown(f"**{request.get('description', request.get('action'))}**")
        st.html(theme.kv_grid(request.get("payload", {})))

        note = st.text_input(
            "Note (optional — sent back to the agent on rejection)",
            key=f"note-{pending.thread_id}",
            placeholder="Why you are rejecting, or any condition on approval",
        )
        approve, reject = st.columns(2)
        if approve.button(
            "Approve and apply",
            type="primary",
            use_container_width=True,
            icon=":material/check:",
        ):
            with st.spinner("Applying..."):
                handle_result(resume_turn(pending.thread_id, approved=True, note=note))
            st.rerun()
        if reject.button(
            "Reject", use_container_width=True, icon=":material/block:"
        ):
            with st.spinner("Cancelling..."):
                handle_result(resume_turn(pending.thread_id, approved=False, note=note))
            st.rerun()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_status(status: dict) -> None:
    if status["llm_configured"]:
        auth = "Vertex AI" if status["auth_mode"] == "vertex_ai" else "AI Studio key"
        st.success(
            f"`{status['model']}` via {auth} · reasoning "
            f"{status['reasoning_effort']}",
            icon=":material/bolt:",
        )
    else:
        st.error(
            "No Google AI credentials. Copy `.env.example` to `.env` and either "
            "set `GOOGLE_API_KEY`, or set `GOOGLE_GENAI_USE_VERTEXAI=true` with "
            "`GOOGLE_CLOUD_PROJECT` and run `gcloud auth application-default "
            "login`. Then restart.",
            icon=":material/key_off:",
        )

    tracing = tracing_status()
    if tracing["active"]:
        st.success(
            f"Tracing to `{tracing['project']}`", icon=":material/monitoring:"
        )
    else:
        st.warning(
            f"Tracing off ({tracing['reason']})", icon=":material/monitoring:"
        )

    if status["data_ok"]:
        st.info(
            f"Data source: `{status['data_source']}` "
            f"({status['shipment_count']} shipments)",
            icon=":material/database:",
        )
    else:
        st.error(f"Data layer error: {status['data_error']}", icon=":material/error:")

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
    theme.rail_label("Conversations")
    conversations = list_conversations(limit=25)
    if not conversations:
        st.caption("No past conversations yet.")
        return

    # One style block colours every row's severity dot; a button label is plain
    # text, so the dot cannot travel with it.
    theme.conversation_dots(
        [(c.thread_id, c.last_severity) for c in conversations]
    )

    current = st.session_state.thread_id
    for conversation in conversations:
        is_current = conversation.thread_id == current
        turns = f"{conversation.turns} turn" + ("" if conversation.turns == 1 else "s")
        row, delete_col = st.columns([5, 1])
        if row.button(
            conversation.title,
            key=f"open-{conversation.thread_id}",
            use_container_width=True,
            type="primary" if is_current else "secondary",
            help=(
                f"{turns} · last activity {conversation.updated_at}"
                + (
                    f" · {conversation.last_severity} severity"
                    if conversation.last_severity
                    else ""
                )
            ),
            disabled=is_current,
        ):
            open_conversation(conversation.thread_id)
            st.rerun()
        if delete_col.button(
            "",
            key=f"del-{conversation.thread_id}",
            help="Delete this conversation",
            icon=":material/delete:",
        ):
            delete_conversation(conversation.thread_id)
            if is_current:
                start_new_conversation()
            st.rerun()


def render_sidebar(status: dict) -> None:
    with st.sidebar:
        theme.brand()
        st.caption("LangGraph multi-agent supply chain platform")

        theme.rail_label("System status")
        render_status(status)
        st.divider()

        if st.button(
            "New conversation",
            use_container_width=True,
            type="primary",
            icon=":material/add:",
        ):
            start_new_conversation()
            st.rerun()

        render_conversation_list()
        st.divider()

        theme.rail_label("This session")
        st.caption(f"Thread: `{st.session_state.thread_id}`")

        if st.session_state.history:
            if st.session_state.renaming:
                new_title = st.text_input("Conversation title", key="rename-input")
                if st.button(
                    "Save title", use_container_width=True, icon=":material/check:"
                ):
                    if new_title.strip():
                        rename_conversation(st.session_state.thread_id, new_title)
                    st.session_state.renaming = False
                    st.rerun()
            elif st.button(
                "Rename", use_container_width=True, icon=":material/edit:"
            ):
                st.session_state.renaming = True
                st.rerun()

            st.download_button(
                "Export transcript",
                data=export_conversation(st.session_state.thread_id),
                file_name=f"{st.session_state.thread_id}.md",
                mime="text/markdown",
                use_container_width=True,
                icon=":material/download:",
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}k"
    return f"${value:,.0f}"


def render_hero(snapshot: Snapshot, *, landing: bool) -> None:
    """The page header. The lane and the counts only show on the landing state.

    Once a conversation is under way the answers matter more than the network
    summary, and a full-height dashboard would push them below the fold.
    """
    with st.container(key="nr-hero"):
        theme.eyebrow("NovaRetail Group · disruption desk")
        st.title("Supply Chain Disruption Management")
        st.caption(
            "Ask about shipments, inventory, suppliers or incidents. One "
            "assistant, six agents behind it."
        )
        if not landing:
            return

        cards = [
            ("Shipments tracked", snapshot.shipments, None),
            ("In transit", snapshot.in_transit, "#38BDF8"),
            (
                "Running late",
                snapshot.delayed,
                "#FB923C" if snapshot.delayed else "#34D399",
            ),
            (
                "Open incidents",
                snapshot.open_incidents,
                "#F87171" if snapshot.critical_incidents else "#FACC15",
            ),
        ]
        st.html(theme.lane(disrupted=snapshot.disrupted) + theme.kpis(cards))
        if snapshot.value_at_risk:
            st.caption(
                f"{money(snapshot.value_at_risk)} of cargo is delayed or damaged "
                "right now — ask about any of it below."
            )


def render_zero_state() -> None:
    """Suggested questions, shown only on an empty conversation.

    They live here rather than in the sidebar because that is where a reader
    looks first on an empty screen, and because a permanent list of canned
    prompts alongside a real conversation reads as a demo.
    """
    theme.rail_label("Start with one of these")
    columns = st.columns(3)
    for index, prompt in enumerate(SAMPLE_PROMPTS):
        if columns[index % 3].button(
            prompt, key=f"ask-{index}", use_container_width=True
        ):
            st.session_state.queued_prompt = prompt
            st.rerun()


def main() -> None:
    init_state()
    theme.inject()
    status = health()
    render_sidebar(status)

    landing = not st.session_state.history
    render_hero(fleet_snapshot(), landing=landing)

    render_history()

    if st.session_state.pending is not None:
        render_approval_gate()
        return

    if landing:
        render_zero_state()

    # Always render the input box, then let a suggested prompt win.
    typed = st.chat_input("Ask about a shipment, warehouse, supplier or incident")
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

    with st.chat_message("assistant"):
        panel = st.empty()
        progress = TurnProgress()

        def show(event) -> None:
            """Repaint the live panel.

            Called synchronously from inside the graph run, which is what lets
            the display move mid-node instead of waiting for a 20-second
            recovery step to finish.
            """
            progress.apply(event)
            panel.html(theme.live_progress(progress, PIPELINE))

        panel.html(theme.live_progress(progress, PIPELINE))
        try:
            result = stream_turn(prompt, st.session_state.thread_id, on_event=show)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the UI
            panel.empty()
            st.session_state.history.append(
                {
                    "role": "assistant",
                    "content": f"Something went wrong: `{exc.__class__.__name__}: {exc}`",
                    "meta": {},
                }
            )
            st.rerun()
        else:
            panel.empty()
            handle_result(result)
    st.rerun()


if __name__ == "__main__":
    main()
