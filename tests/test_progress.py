"""Live progress events.

These run with no model: the tracker is a callback handler, so it can be driven
with the same calls LangChain would make. What matters is that the display
state ends up correct and that a broken display cannot break the turn.
"""

from __future__ import annotations

from supplychain.progress import (
    PIPELINE,
    ProgressEvent,
    ProgressTracker,
    TurnProgress,
)


def drive(tracker: ProgressTracker) -> None:
    """The callback sequence a real two-agent turn produces."""
    tracker.on_chain_start({"name": "intake"}, {})
    tracker.node_finished("intake")
    tracker.on_chain_start({"name": "supervisor"}, {})
    tracker.node_finished("supervisor")
    tracker.on_chain_start({"name": "shipment"}, {})
    tracker.on_tool_start({"name": "track_shipment"}, "")
    tracker.on_tool_start({"name": "find_affected_orders"}, "")
    tracker.node_finished("shipment")


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


def test_pipeline_nodes_become_agent_events():
    tracker = ProgressTracker()
    drive(tracker)

    kinds = [(e.kind, e.name) for e in tracker.events]
    assert ("agent_start", "intake") in kinds
    assert ("agent_done", "intake") in kinds
    assert ("tool", "track_shipment") in kinds


def test_framework_chains_are_not_reported():
    """A progress display naming RunnableSequence helps nobody."""
    tracker = ProgressTracker()
    for name in ("LangGraph", "RunnableSequence", "model", "tools", "__start__"):
        tracker.on_chain_start({"name": name}, {})
    assert tracker.events == []


def test_a_node_outside_the_pipeline_is_ignored():
    tracker = ProgressTracker()
    tracker.node_finished("some_internal_node")
    assert tracker.events == []


def test_the_sink_is_called_as_events_happen():
    seen: list[ProgressEvent] = []
    tracker = ProgressTracker(sink=seen.append)
    drive(tracker)

    assert [e.kind for e in seen] == [e.kind for e in tracker.events]
    assert seen[0].name == "intake"


def test_a_broken_display_cannot_break_the_turn():
    """The panel is decoration; the answer is the deliverable."""

    def explode(_event):
        raise RuntimeError("the UI fell over")

    tracker = ProgressTracker(sink=explode)
    drive(tracker)  # must not raise

    assert len(tracker.events) == 8


def test_rate_limit_waits_are_reported():
    seen: list[ProgressEvent] = []
    tracker = ProgressTracker(sink=seen.append)
    tracker.waiting_out_rate_limit("shipment agent", 1, 60.0, RuntimeError("429"))

    assert seen[-1].kind == "retry"
    assert "60s" in seen[-1].detail
    assert "shipment agent" in seen[-1].detail


def test_events_carry_elapsed_time():
    tracker = ProgressTracker()
    tracker.node_finished("intake")
    assert tracker.events[0].elapsed >= 0


# ---------------------------------------------------------------------------
# Display state
# ---------------------------------------------------------------------------


def test_progress_tracks_who_is_working_now():
    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    drive(tracker)

    assert progress.done == ["intake", "supervisor", "shipment"]
    assert progress.current is None  # shipment finished
    assert progress.tools_for("shipment") == ["track_shipment", "find_affected_orders"]


def test_the_active_agent_is_the_one_not_yet_finished():
    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.on_chain_start({"name": "recovery"}, {})

    assert progress.current == "recovery"
    assert progress.done == []


def test_tools_are_attributed_to_the_agent_that_ran_them():
    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.on_chain_start({"name": "shipment"}, {})
    tracker.on_tool_start({"name": "track_shipment"}, "")
    tracker.node_finished("shipment")
    tracker.on_chain_start({"name": "inventory"}, {})
    tracker.on_tool_start({"name": "check_inventory"}, "")

    assert progress.tools_for("shipment") == ["track_shipment"]
    assert progress.tools_for("inventory") == ["check_inventory"]


def test_a_repeated_tool_is_listed_once():
    """Workers retry tools; the trace is where the exact sequence lives."""
    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.on_chain_start({"name": "shipment"}, {})
    for _ in range(3):
        tracker.on_tool_start({"name": "track_shipment"}, "")

    assert progress.tools_for("shipment") == ["track_shipment"]


def test_an_agent_is_not_listed_twice():
    progress = TurnProgress()
    for _ in range(2):
        progress.apply(ProgressEvent(kind="agent_done", name="supervisor"))
    assert progress.done == ["supervisor"]


def test_notices_collect_rate_limit_messages():
    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.waiting_out_rate_limit("recovery agent", 2, 30.0, RuntimeError("429"))

    assert progress.notices and "30s" in progress.notices[-1]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_the_panel_lists_only_agents_that_ran():
    from supplychain.ui import theme

    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    drive(tracker)

    html = theme.live_progress(progress, PIPELINE)
    assert "Shipment" in html and "Intake" in html
    # Nothing implies the other agents were involved.
    assert "Supplier" not in html and "Recovery" not in html


def test_the_panel_shows_the_tools_in_flight():
    from supplychain.ui import theme

    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.on_chain_start({"name": "shipment"}, {})
    tracker.on_tool_start({"name": "track_shipment"}, "")

    html = theme.live_progress(progress, PIPELINE)
    assert "track_shipment" in html
    assert "nr-step--active" in html


def test_the_panel_has_something_to_say_before_anything_happens():
    from supplychain.ui import theme

    html = theme.live_progress(TurnProgress(), PIPELINE)
    assert "Reading the request" in html


def test_the_panel_surfaces_a_rate_limit_wait():
    from supplychain.ui import theme

    progress = TurnProgress()
    ProgressTracker(sink=progress.apply).waiting_out_rate_limit(
        "shipment agent", 1, 60.0, RuntimeError("429")
    )
    html = theme.live_progress(progress, PIPELINE)
    assert "nr-live-notice" in html and "60s" in html


def test_the_panel_escapes_tool_names():
    from supplychain.ui import theme

    progress = TurnProgress()
    tracker = ProgressTracker(sink=progress.apply)
    tracker.on_chain_start({"name": "shipment"}, {})
    tracker.on_tool_start({"name": "<script>alert(1)</script>"}, "")

    html = theme.live_progress(progress, PIPELINE)
    assert "<script>" not in html


def test_every_pipeline_name_has_an_icon():
    from supplychain.ui import theme

    for name in PIPELINE:
        assert name in theme.AGENT_META, f"{name} would render as a bullet"
