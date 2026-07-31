"""Run the evaluation conversation set through the graph.

Satisfies the LangSmith requirements in the brief in one command: records at
least 10 traced conversations, measures latency per turn, and reports which
runs failed and which agents were used.

    python scripts/run_eval_conversations.py                # all cases
    python scripts/run_eval_conversations.py --case delay_impact
    python scripts/run_eval_conversations.py --no-approve   # reject every write

Each conversation runs on its own thread, tagged ``eval`` plus the case name,
so they are easy to filter in the LangSmith UI. Results are also written to
``docs/eval_runs.json`` for the LangSmith report.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supplychain.observability import tracing_status  # noqa: E402
from supplychain.runner import health, resume_turn, run_turn  # noqa: E402

# Ten conversations covering every functional requirement in the brief, plus
# the unsupported-request and missing-information edge cases.
CASES: dict[str, list[str]] = {
    "track_shipment": [
        "What's the status of shipment SHP-2026-0002?",
    ],
    "delay_impact": [
        "SHP-2026-0002 is late into WH-N02. What's the impact and what should we do?",
    ],
    "affected_orders": [
        "Which orders are affected by the delay on SHP-2026-0001?",
    ],
    "delay_backlog": [
        "Which shipments are delayed right now, and which warehouse is worst hit?",
    ],
    "inventory_shortage": [
        "We're short on SKU-1001 at WH-N04. Can we cover it from another warehouse?",
    ],
    "warehouse_availability": [
        "How is stock looking at WH-N02 - anything below reorder point?",
    ],
    "supplier_failure": [
        "SUP-005 has missed two delivery windows. Find me alternatives for SKU-3001 "
        "and compare them on cost and lead time.",
    ],
    "damaged_goods": [
        "SHP-2026-0005 arrived damaged at WH-N08. Assess the loss and raise an incident.",
    ],
    "incident_status": [
        "What's the status of incident INC-2026-0001, and should it be escalated?",
    ],
    "multi_turn_memory": [
        "What's the status of SHP-2026-0002?",
        "Who's the supplier on that shipment, and are they reliable?",
        "What would it cost to source the same SKUs somewhere else?",
    ],
    "unsupported_request": [
        "Can you reset my email password?",
    ],
    "missing_information": [
        "A shipment is late, can you look into it?",
    ],
}


# A multi-agent turn is many model calls, and the Gemini free tier allows only
# 15 requests/minute - so a 12-conversation run hits the quota without this.
QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "429", "quota")
MAX_QUOTA_RETRIES = 4
RETRY_DELAY_PATTERN = re.compile(r"[Rr]etry in ([0-9.]+)s")


def is_quota_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in QUOTA_MARKERS)


def retry_delay_from(exc: Exception, attempt: int) -> float:
    """Honour the server's suggested delay, else back off exponentially."""
    match = RETRY_DELAY_PATTERN.search(str(exc))
    if match:
        return float(match.group(1)) + 1.0
    return min(60.0, 5.0 * (2**attempt))


def with_quota_retry(call, *, label: str):
    """Run a turn, waiting out rate limits rather than failing the case."""
    last: Exception | None = None
    for attempt in range(MAX_QUOTA_RETRIES):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if not is_quota_error(exc):
                raise
            last = exc
            delay = retry_delay_from(exc, attempt)
            print(
                f"    rate limited on {label}; waiting {delay:.1f}s "
                f"(attempt {attempt + 1}/{MAX_QUOTA_RETRIES})"
            )
            time.sleep(delay)
    raise last  # type: ignore[misc]


def run_case(name: str, turns: list[str], *, approve: bool, pace: float = 0.0) -> dict:
    thread_id = f"eval-{name}"
    records = []
    started = time.perf_counter()

    for index, message in enumerate(turns, start=1):
        if pace and index > 1:
            time.sleep(pace)
        try:
            result = with_quota_retry(
                lambda: run_turn(message, thread_id, tags=["eval", name]),
                label=f"{name} turn {index}",
            )
            approvals = 0
            while result.awaiting_approval:
                approvals += 1
                result = with_quota_retry(
                    lambda: resume_turn(
                        thread_id,
                        approved=approve,
                        note="" if approve else "rejected by the evaluation harness",
                        tags=["eval", name],
                    ),
                    label=f"{name} approval {approvals}",
                )
            records.append(
                {
                    "turn": index,
                    "message": message,
                    "answer": result.answer,
                    "route": result.route,
                    "severity": result.severity,
                    "hops": result.hops,
                    "approvals_handled": approvals,
                    "executed_actions": [a.get("action") for a in result.executed_actions],
                    "latency_seconds": result.latency_seconds,
                    "ok": result.ok,
                }
            )
            print(
                f"  turn {index}: {result.latency_seconds}s "
                f"route={'→'.join(result.route) or 'none'} "
                f"{'OK' if result.ok else 'FAILED'}"
            )
        except Exception as exc:  # noqa: BLE001 - a failed run is a result
            records.append(
                {
                    "turn": index,
                    "message": message,
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "ok": False,
                }
            )
            print(f"  turn {index}: ERROR {exc.__class__.__name__}: {exc}")

    return {
        "case": name,
        "thread_id": thread_id,
        "turns": records,
        "total_seconds": round(time.perf_counter() - started, 2),
        "ok": all(r.get("ok") for r in records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="run only these cases")
    parser.add_argument(
        "--no-approve",
        action="store_true",
        help="reject every approval request instead of approving it",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "eval_runs.json"),
        help="where to write the results JSON",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help=(
            "seconds to wait between turns. The Gemini free tier allows 15 "
            "requests/minute and one multi-agent turn is several requests, so "
            "try --pace 20 for a full run on a free-tier key."
        ),
    )
    args = parser.parse_args()

    status = health()
    if not status["llm_configured"]:
        print("GOOGLE_API_KEY is not set - nothing to evaluate.", file=sys.stderr)
        return 2

    tracing = tracing_status()
    print(
        f"provider={status['provider']} model={status['model']} "
        f"reasoning={status['reasoning_effort']} data={status['data_source']}"
    )
    print(
        f"langsmith tracing: {'on → ' + tracing['project'] if tracing['active'] else 'OFF (' + str(tracing['reason']) + ')'}"
    )
    if not tracing["active"]:
        print(
            "  warning: the brief requires traced conversations. Set LANGSMITH_TRACING=true "
            "and LANGSMITH_API_KEY before the graded run."
        )
    print()

    selected = args.case or list(CASES)
    unknown = [name for name in selected if name not in CASES]
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    results = []
    for name in selected:
        print(f"[{name}]")
        results.append(
            run_case(
                name, CASES[name], approve=not args.no_approve, pace=args.pace
            )
        )
        print()

    latencies = [
        turn["latency_seconds"]
        for case in results
        for turn in case["turns"]
        if turn.get("latency_seconds")
    ]
    failures = [case["case"] for case in results if not case["ok"]]

    summary = {
        "provider": status["provider"],
        "model": status["model"],
        "reasoning_effort": status["reasoning_effort"],
        "tracing_project": tracing["project"],
        "conversations": len(results),
        "turns": sum(len(case["turns"]) for case in results),
        "failed_cases": failures,
        "latency_seconds": {
            "count": len(latencies),
            "min": min(latencies) if latencies else None,
            "median": round(statistics.median(latencies), 2) if latencies else None,
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p95": (
                round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 2)
                if len(latencies) >= 2
                else None
            ),
            "max": max(latencies) if latencies else None,
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"summary": summary, "cases": results}, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=" * 60)
    print(f"conversations : {summary['conversations']}")
    print(f"turns         : {summary['turns']}")
    print(f"latency (s)   : median {summary['latency_seconds']['median']} "
          f"| mean {summary['latency_seconds']['mean']} "
          f"| max {summary['latency_seconds']['max']}")
    print(f"failed cases  : {', '.join(failures) if failures else 'none'}")
    print(f"results       : {out_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
