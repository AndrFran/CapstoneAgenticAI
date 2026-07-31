"""Turn runner behaviour, driven against a scripted graph.

These run with no API key and no real graph: the point is the bookkeeping
`runner` does *around* the graph - registering the conversation, recording each
turn's trace metadata, and pairing that metadata back onto the right answer.

That pairing is positional, which is why the gated-turn case below matters: a
turn that goes through the approval gate produces its assistant message on the
`resume_turn` call, so a meta that is never written there does not just lose one
trace - it slides every later trace onto the wrong answer.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from supplychain import memory, runner


class FakeSnapshot:
    def __init__(self, values: dict) -> None:
        self.values = values


class ScriptedGraph:
    """A graph that answers, or stops at an approval gate, on cue."""

    def __init__(self) -> None:
        self.state: dict = {"messages": []}
        self.gate_next = False
        self.invocations = 0

    def invoke(self, payload, config=None):  # noqa: ANN001 - test double
        self.invocations += 1
        incoming = payload.get("messages", []) if isinstance(payload, dict) else []
        self.state["messages"] = [*self.state["messages"], *incoming]

        if self.gate_next:
            self.gate_next = False
            self.state["pending_action"] = {"action": "create_incident"}
            return {
                "__interrupt__": (
                    {"type": "approval_request", "action": "create_incident"},
                )
            }

        answer = f"answer {self.invocations}"
        self.state["final_response"] = answer
        self.state["pending_action"] = None
        self.state["severity"] = "high"
        self.state["visited"] = ["shipment"]
        self.state["hops"] = 2
        self.state["messages"] = [*self.state["messages"], AIMessage(content=answer)]
        return {}

    def stream(self, payload, config=None, stream_mode=None):  # noqa: ANN001
        """Yield per-node updates the way LangGraph does, then finish."""
        gated = self.gate_next
        result = self.invoke(payload, config)
        yield {"intake": {"request": {}}}
        yield {"supervisor": {"next_agent": "shipment"}}
        yield {"shipment": {"findings": {}}}
        if gated:
            yield {"__interrupt__": result["__interrupt__"]}
        else:
            yield {"respond": {"final_response": self.state.get("final_response")}}

    def get_state(self, _config):  # noqa: ANN001 - test double
        return FakeSnapshot(dict(self.state))


def install(monkeypatch) -> ScriptedGraph:
    graph = ScriptedGraph()
    monkeypatch.setattr(runner, "get_compiled_graph", lambda: graph)
    return graph


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_a_plain_turn_is_recorded(monkeypatch):
    install(monkeypatch)
    runner.run_turn("where is SHP-2026-0002?", "t-plain")

    metas = memory.get_store().turn_meta("t-plain")
    assert len(metas) == 1
    assert metas[0]["severity"] == "high"
    assert metas[0]["route"] == ["shipment"]


def test_a_gated_turn_records_nothing_until_it_is_resolved(monkeypatch):
    graph = install(monkeypatch)
    graph.gate_next = True

    turn = runner.run_turn("raise an incident", "t-gate")

    assert turn.awaiting_approval is True
    assert turn.approval_request["action"] == "create_incident"
    # Nothing is recorded yet - the turn has not produced an answer.
    assert memory.get_store().turn_meta("t-gate") == []


def test_resuming_a_gated_turn_records_it(monkeypatch):
    """The regression: this used to be skipped entirely."""
    graph = install(monkeypatch)
    graph.gate_next = True
    runner.run_turn("raise an incident", "t-resume")

    turn = runner.resume_turn("t-resume", approved=True)

    assert turn.awaiting_approval is False
    metas = memory.get_store().turn_meta("t-resume")
    assert len(metas) == 1
    assert metas[0]["hops"] == 2


def test_a_rejected_action_still_records_the_turn(monkeypatch):
    graph = install(monkeypatch)
    graph.gate_next = True
    runner.run_turn("raise an incident", "t-reject")

    runner.resume_turn("t-reject", approved=False, note="not now")

    assert len(memory.get_store().turn_meta("t-reject")) == 1


# ---------------------------------------------------------------------------
# The pairing this protects
# ---------------------------------------------------------------------------


def test_metadata_stays_aligned_after_a_gated_turn(monkeypatch):
    """Turn 1 goes through the gate, turn 2 does not. Both keep their own trace.

    Before `resume_turn` recorded anything, turn 2's metadata was the only one
    stored, so it was rendered under turn 1's answer.
    """
    graph = install(monkeypatch)

    graph.gate_next = True
    runner.run_turn("raise an incident", "t-align")
    runner.resume_turn("t-align", approved=True)
    runner.run_turn("and what is the status now?", "t-align")

    turns = runner.conversation_turns("t-align")
    answers = [t for t in turns if t["role"] == "assistant"]

    assert len(answers) == 2
    assert all(turn["meta"] for turn in answers), "an answer lost its trace"
    # Each answer carries its own trace, not the next one's.
    assert answers[0]["content"] == "answer 2"
    assert answers[1]["content"] == "answer 3"
    assert len(memory.get_store().turn_meta("t-align")) == 2


def test_the_conversation_index_counts_a_gated_turn(monkeypatch):
    graph = install(monkeypatch)
    graph.gate_next = True
    runner.run_turn("raise an incident", "t-count")
    runner.resume_turn("t-count", approved=True)

    record = runner.get_conversation("t-count")
    assert record is not None
    assert record.turns == 1
    assert record.last_severity == "high"


# ---------------------------------------------------------------------------
# Conversation registration
# ---------------------------------------------------------------------------


def test_the_first_message_becomes_the_title(monkeypatch):
    install(monkeypatch)
    runner.run_turn("Which shipments are delayed right now?", "t-title")

    record = runner.get_conversation("t-title")
    assert record is not None
    assert "delayed" in record.title.lower()


def test_a_later_message_does_not_rename_the_conversation(monkeypatch):
    install(monkeypatch)
    runner.run_turn("Which shipments are delayed right now?", "t-title2")
    first = runner.get_conversation("t-title2").title

    runner.run_turn("and the supplier?", "t-title2")

    assert runner.get_conversation("t-title2").title == first


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_streaming_reports_each_agent_as_it_finishes(monkeypatch):
    install(monkeypatch)
    seen = []

    turn = runner.stream_turn(
        "where is SHP-2026-0002?", "t-stream", on_event=seen.append
    )

    assert turn.answer
    names = [e.name for e in seen if e.kind == "agent_done"]
    assert names == ["intake", "supervisor", "shipment", "respond"]


def test_streaming_records_the_turn_like_run_turn(monkeypatch):
    install(monkeypatch)
    runner.stream_turn("where is SHP-2026-0002?", "t-stream2")

    metas = memory.get_store().turn_meta("t-stream2")
    assert len(metas) == 1
    assert metas[0]["severity"] == "high"


def test_streaming_surfaces_an_approval_gate(monkeypatch):
    """The interrupt arrives as a stream update, not as a return value."""
    graph = install(monkeypatch)
    graph.gate_next = True

    turn = runner.stream_turn("raise an incident", "t-stream3")

    assert turn.awaiting_approval is True
    assert turn.approval_request["action"] == "create_incident"
    # Nothing recorded until the human decides.
    assert memory.get_store().turn_meta("t-stream3") == []


def test_streaming_registers_the_conversation(monkeypatch):
    install(monkeypatch)
    runner.stream_turn("Which shipments are delayed right now?", "t-stream4")

    record = runner.get_conversation("t-stream4")
    assert record is not None and "delayed" in record.title.lower()


def test_streaming_clears_the_retry_reporter_afterwards(monkeypatch):
    """A stale reporter would push events into a dead Streamlit placeholder."""
    from supplychain import resilience

    install(monkeypatch)
    runner.stream_turn("hello", "t-stream5", on_event=lambda _e: None)

    assert resilience._RETRY_REPORTER is None


def test_streaming_works_without_a_listener(monkeypatch):
    install(monkeypatch)
    assert runner.stream_turn("hello", "t-stream6").answer


def test_conversation_turns_skips_empty_messages(monkeypatch):
    graph = install(monkeypatch)
    runner.run_turn("hello", "t-empty")
    graph.state["messages"] = [
        *graph.state["messages"],
        AIMessage(content="   "),
        HumanMessage(content=""),
    ]

    roles = [turn["role"] for turn in runner.conversation_turns("t-empty")]
    assert roles == ["user", "assistant"]
