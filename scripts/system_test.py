"""End-to-end system test for the integrated platform.

The unit suite proves each part behaves; this proves the parts behave
*together*. It runs the real graph against the real model over the real SQLite
memory back end, so it needs `GOOGLE_API_KEY` (or the Vertex AI setup) and it
costs real requests - which is why it is a script rather than a pytest module.

    python scripts/system_test.py
    python scripts/system_test.py --pace 20   # free tier: 15 requests/minute

Two pieces of state are handled so a run is repeatable:

* The conversation database is redirected to a temporary directory, so a run
  does not pollute your own history.
* The runtime write store is **reset at the start**. Scenario 3 approves a real
  incident, and leaving it behind makes the next run fail in a way that looks
  like a defect but is not: the incident agent's duplicate check finds the
  incident the previous run created and correctly refuses to raise a second
  one. Committed fixtures are untouched either way - only
  `data/runtime/` is cleared, which `pytest` also does around every test.

Seven scenarios, ordered cheapest first so a quota failure still leaves useful
signal:

1. A read-only status query stays read-only and does not sprawl.
2. Entity memory resolves "that shipment" on a later turn.
3. The approval gate writes nothing until approved, then records the turn.
4. A rejected write changes nothing and is not claimed as done.
5. Unsupported requests and unknown identifiers are handled, not crashed on.
6. A transient rate limit is waited out (fault injection).
7. A dead worker does not kill the turn (fault injection).

Owner: Team Member 4 (cross-agent error handling), used by all four.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Redirect conversation memory before anything reads settings - get_settings()
# is lru_cached on first call.
SCRATCH = Path(tempfile.mkdtemp(prefix="novaretail-systest-"))
os.environ["SUPPLYCHAIN_MEMORY_BACKEND"] = "sqlite"
os.environ["SUPPLYCHAIN_MEMORY_PATH"] = str(SCRATCH / "conversations.sqlite")
os.environ.setdefault("SUPPLYCHAIN_LLM_RETRY_BASE_DELAY", "1")

from supplychain import config, memory, runner  # noqa: E402
from supplychain.agents import base  # noqa: E402
from supplychain.data import access  # noqa: E402

config.reload_settings()

RESULTS: list[tuple[str, bool, str]] = []
PACE = 8.0


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"    {'PASS' if ok else 'FAIL'}  {name}" + (f" - {detail}" if detail else ""))


def scenario(title: str) -> None:
    print(f"\n[{title}]")
    time.sleep(PACE)


def settle(thread_id: str, *, approved: bool, note: str = "", max_rounds: int = 3):
    """Answer approval requests until the turn actually ends.

    One decision does not always finish a turn: a recovery plan can propose an
    incident *and* a reroute, so rejecting the first legitimately surfaces the
    second. The gate is per-action by design - a human should see each write
    separately - so a caller has to drain it rather than assume one round.
    """
    result = None
    for round_number in range(1, max_rounds + 1):
        time.sleep(PACE)
        result = runner.resume_turn(thread_id, approved=approved, note=note)
        if not result.awaiting_approval:
            if round_number > 1:
                print(f"    (took {round_number} approval rounds)")
            return result
    print(f"    (still gated after {max_rounds} rounds)")
    return result


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def s1_status_query() -> None:
    scenario("1. Read-only status query")
    r = runner.run_turn("What's the status of shipment SHP-2026-0002?", "sys-1")
    print(f"    route={'→'.join(r.route) or 'none'} hops={r.hops} {r.latency_seconds}s")

    check("answers without an approval gate", not r.awaiting_approval)
    check("produced an answer", bool(r.answer))
    check("quotes the shipment id", "SHP-2026-0002" in (r.answer or ""))
    check("mentions the 72h delay", "72" in (r.answer or ""))
    check("stays within the worker budget", len(r.route) <= 2, f"route={r.route}")
    check("never reaches recovery", "recovery" not in r.route)
    check(
        "intake extracted the id",
        "SHP-2026-0002" in (r.request or {}).get("shipment_ids", []),
    )


def s2_entity_memory() -> None:
    scenario("2. Conversation memory across turns")
    runner.run_turn("What's the status of SHP-2026-0002?", "sys-2")
    time.sleep(PACE)
    r = runner.run_turn("who is the supplier on that shipment?", "sys-2")
    print(f"    resolved_from_memory={r.resolved_from_memory}")

    check("second turn produced an answer", bool(r.answer))
    entities = runner.conversation_entities("sys-2")
    check(
        "shipment carried in entity memory",
        "SHP-2026-0002" in entities.get("shipment_ids", []),
        str(entities.get("shipment_ids")),
    )
    check(
        "resolved the reference without a repeated id",
        bool(r.resolved_from_memory) or "SHP-2026-0002" in (r.answer or ""),
    )


def s3_approval_gate_and_trace() -> None:
    scenario("3. Human-in-the-loop write + trace recording")
    before = len(access.load("incidents"))
    r = runner.run_turn(
        "SHP-2026-0005 arrived damaged at WH-N08. Assess the loss and raise an incident.",
        "sys-3",
    )
    if not r.awaiting_approval:
        check("reached the approval gate", False, f"answered directly: {(r.answer or '')[:80]}")
        return

    check("reached the approval gate", True, r.approval_request.get("action", ""))
    check("nothing written before approval", len(access.load("incidents")) == before)

    r2 = settle("sys-3", approved=True)
    check("turn completed after approval", bool(r2.answer))
    check("the write was applied", len(access.load("incidents")) == before + 1)
    check("executed action reported", bool(r2.executed_actions))

    # A gated turn must record its trace, or - because conversation_turns pairs
    # metadata to assistant messages by position - every later turn's trace
    # slides onto the wrong answer.
    check("gated turn recorded its trace", len(memory.get_store().turn_meta("sys-3")) == 1)
    record = runner.get_conversation("sys-3")
    check(
        "conversation index counts the turn",
        bool(record) and record.turns == 1,
        f"turns={getattr(record, 'turns', None)}",
    )

    time.sleep(PACE)
    runner.run_turn("what is the severity of that incident?", "sys-3")
    answers = [t for t in runner.conversation_turns("sys-3") if t["role"] == "assistant"]
    check(
        "every answer kept its own trace",
        all(t["meta"] for t in answers),
        f"{sum(1 for t in answers if t['meta'])}/{len(answers)} have metadata",
    )


def s4_rejection() -> None:
    scenario("4. Rejected write changes nothing")
    before = len(access.load("incidents"))
    r = runner.run_turn(
        "SHP-2026-0001 is badly delayed into WH-N04. Raise an incident for it.", "sys-4"
    )
    if not r.awaiting_approval:
        check("reached the approval gate", False, "answered directly")
        return

    r2 = settle("sys-4", approved=False, note="handled offline")
    check("no write applied on rejection", len(access.load("incidents")) == before)
    check("answered after rejection", bool(r2.answer))
    # Assert the positive. Hunting for phrases that would be a false claim is
    # negation-blind - "no incident has been created" is the correct answer and
    # contains "has been created". The data layer above is the hard evidence
    # that nothing ran; this checks the operator is told so.
    lowered = (r2.answer or "").lower()
    check(
        "answer states the action did not happen",
        any(
            s in lowered
            for s in ("reject", "not been created", "remains uncreated", "no incident",
                      "not created", "cancelled", "did not create", "was not")
        ),
        (r2.answer or "")[:90],
    )


def s5_guardrails() -> None:
    scenario("5. Unsupported request and unknown identifier")
    r = runner.run_turn("Can you reset my email password?", "sys-5")
    check("unsupported request declined", not r.awaiting_approval and bool(r.answer))
    check("intake marked it unsupported", (r.request or {}).get("is_supported") is False)

    time.sleep(PACE)
    r2 = runner.run_turn("check shipment SHP-9999-9999 please", "sys-6")
    check("unknown id caught at intake", bool(r2.unknown_identifiers))
    check("answered instead of failing", bool(r2.answer))


class _FlakyAgent:
    """A worker that throws a quota error before succeeding."""

    def __init__(self, failures: int, exc: Exception) -> None:
        self.failures, self.exc, self.calls = failures, exc, 0

    def invoke(self, _payload):  # noqa: ANN001
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage(content="Recovered: shipment is delayed 72h.")]}


class _DeadAgent:
    def invoke(self, _payload):  # noqa: ANN001
        raise RuntimeError("400 INVALID_ARGUMENT: permanently broken")


def _with_worker(agent, call):
    original = base.get_worker
    base.get_worker = lambda _name: agent
    try:
        return call()
    finally:
        base.get_worker = original


def s6_transient_retry() -> None:
    scenario("6. Fault injection - a transient 429 is waited out")
    agent = _FlakyAgent(2, RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
    r = _with_worker(
        agent, lambda: runner.run_turn("What's the status of SHP-2026-0002?", "sys-7")
    )
    print(f"    worker invoked {agent.calls}x")

    check("retried past the rate limit", agent.calls == 3, f"{agent.calls} attempts")
    check("turn still produced an answer", bool(r.answer))
    check(
        "no agent marked failed",
        not any(f.get("failed") for f in (r.findings or {}).values() if isinstance(f, dict)),
    )


def s7_worker_containment() -> None:
    scenario("7. Fault injection - a dead worker does not kill the turn")
    r = _with_worker(
        _DeadAgent(), lambda: runner.run_turn("What's the status of SHP-2026-0002?", "sys-8")
    )
    findings = r.findings or {}
    failed = [n for n, f in findings.items() if isinstance(f, dict) and f.get("failed")]
    answer = (r.answer or "")
    print(f"    failed agents: {failed}")
    print(f"    answer: {answer[:160]}")

    check("still produced an answer", bool(answer))
    check("the failure was recorded as a finding", bool(failed), str(failed))
    check("no agent retried after failing", len(r.route) == len(set(r.route)), str(r.route))
    lowered = answer.lower()
    check(
        "the answer admits the gap",
        any(p in lowered for p in ("unable", "could not", "couldn't", "not able", "failed")),
    )
    # Nothing runs after the responder, so a promise to keep working is a lie.
    promises = [
        p
        for p in ("attempting to re-query", "re-querying", "i am retrying", "i will retry",
                  "one moment", "please hold", "will update you")
        if p in lowered
    ]
    check("the answer promises no work it cannot do", not promises, str(promises))


SCENARIOS = (
    s1_status_query,
    s2_entity_memory,
    s3_approval_gate_and_trace,
    s4_rejection,
    s5_guardrails,
    s6_transient_retry,
    s7_worker_containment,
)


def main() -> int:
    global PACE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pace",
        type=float,
        default=8.0,
        help="seconds between turns; the Gemini free tier allows 15/minute",
    )
    PACE = parser.parse_args().pace

    status = runner.health()
    print("=" * 70)
    print("NovaRetail end-to-end system test")
    print("=" * 70)
    print(f"model    : {status['model']} ({status['auth_mode']}, "
          f"reasoning {status['reasoning_effort']})")
    print(f"data     : {status['data_source']} - {status['shipment_count']} shipments")
    print(f"memory   : {status['memory_backend']} at {SCRATCH}")
    print(f"approval : {'on' if status['require_approval'] else 'off'} · "
          f"max hops {status['max_hops']}")
    if not status["llm_configured"]:
        print("\nNo LLM credentials configured - a system test needs a live model.")
        return 2

    # Start from the committed fixtures. Without this, scenario 3's approved
    # incident survives into the next run and the duplicate check - correctly -
    # refuses to raise another, which reads as a failure but is not.
    access.reset_runtime_store()
    print("runtime write store reset; starting from the committed fixtures")

    started = time.perf_counter()
    for fn in SCENARIOS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a broken scenario is a result
            check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failures = [(n, d) for n, ok, d in RESULTS if not ok]
    print("\n" + "=" * 70)
    print(f"{passed}/{len(RESULTS)} checks passed in {time.perf_counter() - started:.1f}s")
    for name, detail in failures:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
