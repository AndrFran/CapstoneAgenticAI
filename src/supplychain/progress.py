"""Live progress events for a turn in flight.

A multi-agent turn takes tens of seconds, and until now the UI showed one
spinner for all of it. That is the wrong thing to hide: the interesting claim
this project makes is that six agents collaborate, and a single spinner is
indistinguishable from one slow model call.

Two sources are needed, because neither is sufficient alone:

* **`graph.stream(stream_mode="updates")`** yields one event per *node*, but
  only once that node has finished. On a 20-second recovery step it says
  nothing for 20 seconds.
* **A callback handler** fires *during* a node - every tool call, every model
  call. It reaches the worker agents too, which matters because `run_worker`
  invokes each agent as a separately compiled graph inside a node, so
  `stream(subgraphs=True)` cannot see inside it. Callbacks propagate through
  the call context and can.

So the tracker below is a callback handler, and the runner interleaves its
events with the node events from the stream.

Deliberately knows nothing about Streamlit: it takes a `sink` callable and
hands it events. That keeps it unit-testable with no UI and no model.

Owner: Team Member 1 (chat UI), consuming Team Member 4's runner.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler

# Node names that are workflow plumbing rather than something a person cares
# about seeing.
HIDDEN_CHAINS = {
    "LangGraph",
    "RunnableSequence",
    "RunnableParallel",
    "RunnableLambda",
    "ChannelWrite",
    "model",
    "tools",
    "__start__",
}

# The agents worth naming in a progress display, in workflow order.
PIPELINE = (
    "intake",
    "supervisor",
    "incident_analysis",
    "shipment",
    "inventory",
    "supplier",
    "recovery",
    "approval",
    "respond",
)


@dataclass(frozen=True)
class ProgressEvent:
    """Something worth telling the operator about, while they wait."""

    kind: str  # agent_start | agent_done | tool | retry | error
    name: str
    detail: str = ""
    elapsed: float = 0.0

    @property
    def is_agent(self) -> bool:
        return self.kind in {"agent_start", "agent_done"}


@dataclass
class TurnProgress:
    """The state a progress display renders: who has run, who is running now."""

    started: float = field(default_factory=time.perf_counter)
    current: str | None = None
    done: list[str] = field(default_factory=list)
    tools: dict[str, list[str]] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def tools_for(self, agent: str) -> list[str]:
        return self.tools.get(agent, [])

    def apply(self, event: ProgressEvent) -> None:
        if event.kind == "agent_start":
            self.current = event.name
        elif event.kind == "agent_done":
            if event.name not in self.done:
                self.done.append(event.name)
            if self.current == event.name:
                self.current = None
        elif event.kind == "tool":
            agent = self.current or "?"
            calls = self.tools.setdefault(agent, [])
            # A worker often calls the same tool twice; showing it twice is
            # noise, and the trace is where the exact sequence lives.
            if event.name not in calls:
                calls.append(event.name)
        elif event.kind in {"retry", "error"}:
            self.notices.append(event.detail or event.name)


class ProgressTracker(BaseCallbackHandler):
    """Turns LangChain callbacks into `ProgressEvent`s.

    `sink` is called synchronously on the same thread, which is what lets a
    Streamlit placeholder repaint in the middle of a node rather than waiting
    for it to finish.
    """

    def __init__(self, sink: Callable[[ProgressEvent], Any] | None = None) -> None:
        self.sink = sink
        self.events: list[ProgressEvent] = []
        self.started = time.perf_counter()

    # -- emission ---------------------------------------------------------

    def emit(self, kind: str, name: str, detail: str = "") -> None:
        event = ProgressEvent(
            kind=kind,
            name=name,
            detail=detail,
            elapsed=time.perf_counter() - self.started,
        )
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:  # noqa: BLE001 - a broken display must not
                pass          # take down the turn it is describing

    # -- LangChain callbacks ---------------------------------------------

    def on_chain_start(self, serialized, inputs, **kwargs) -> None:  # noqa: ANN001
        name = (serialized or {}).get("name") or kwargs.get("name")
        if name in PIPELINE:
            self.emit("agent_start", name)

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # noqa: ANN001
        name = (serialized or {}).get("name") or kwargs.get("name")
        if name:
            self.emit("tool", str(name))

    def on_tool_error(self, error, **kwargs) -> None:  # noqa: ANN001
        self.emit("error", "tool", str(error)[:120])

    # -- called by the runner, not by LangChain ---------------------------

    def node_finished(self, node: str) -> None:
        if node in PIPELINE:
            self.emit("agent_done", node)

    def waiting_out_rate_limit(
        self, label: str, attempt: int, delay: float, exc: BaseException | None = None
    ) -> None:
        """Wired to `resilience.call_with_retry`'s on_retry hook.

        Worth surfacing rather than hiding: on a free-tier key this is a
        60-second silence, and an operator staring at a spinner has no way to
        tell it from a hang.
        """
        self.emit(
            "retry",
            label,
            f"Rate limited on {label}; retrying in {delay:.0f}s (attempt {attempt}).",
        )
