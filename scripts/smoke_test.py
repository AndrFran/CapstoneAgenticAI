"""Fast pre-flight check - no API key needed.

Verifies the data layer loads, every tool answers, and the graph compiles.
Run this first when something looks broken; it isolates configuration and data
problems from model problems.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Tracing off, before anything imports LangChain.
#
# This script calls the 34 tools directly rather than through an agent, so
# there is no run for them to hang under: with tracing on, each one arrives in
# LangSmith as its own *root* run. One smoke test buries a day of real
# conversations under 34 orphans, and the project view is where the evaluation
# evidence is read from. There is nothing to learn from tracing a deterministic
# function anyway - that is what the assertions below are for.
#
# `config` calls load_dotenv(override=False), so setting these first means a
# LANGSMITH_TRACING=true in .env cannot turn it back on. Set
# SUPPLYCHAIN_SMOKE_TRACING=1 if you are debugging the tracer itself.
if os.getenv("SUPPLYCHAIN_SMOKE_TRACING", "").lower() not in ("1", "true", "yes"):
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from supplychain.agents.base import AGENT_TOOLS  # noqa: E402
from supplychain.data import access  # noqa: E402
from supplychain.graph import build_graph  # noqa: E402
from supplychain.runner import health  # noqa: E402
from supplychain.tools import ALL_TOOLS  # noqa: E402

CHECKS = [
    ("track_shipment", {"shipment_id": "SHP-2026-0002"}),
    ("check_shipment_delay", {"shipment_id": "SHP-2026-0002"}),
    ("find_affected_orders", {"shipment_id": "SHP-2026-0002"}),
    ("check_delivery_route", {"route_id": "RTE-105"}),
    ("check_inventory", {"sku": "SKU-1001"}),
    ("find_inventory_transfer", {"sku": "SKU-1001", "warehouse_id": "WH-N04", "quantity": 500}),
    ("get_supplier_details", {"supplier_id": "SUP-005"}),
    ("find_alternative_supplier", {"sku": "SKU-3001", "exclude_supplier_id": "SUP-005"}),
    (
        "classify_incident_severity",
        {"incident_type": "shipment_delay", "shipment_id": "SHP-2026-0002"},
    ),
    (
        "generate_recovery_plan",
        {
            "incident_type": "shipment_delay",
            "shipment_id": "SHP-2026-0002",
            "required_units": 1200,
        },
    ),
]


def main() -> int:
    failures = 0

    print("data layer")
    for collection in access.COLLECTIONS:
        try:
            print(f"  {collection:<12} {len(access.load(collection)):>4} records")
        except Exception as exc:  # noqa: BLE001
            print(f"  {collection:<12} FAILED: {exc}")
            failures += 1

    print("\ntools")
    by_name = {tool.name: tool for tool in ALL_TOOLS}
    for name, kwargs in CHECKS:
        tool = by_name.get(name)
        if tool is None:
            print(f"  {name:<28} MISSING")
            failures += 1
            continue
        try:
            payload = json.loads(tool.invoke(kwargs))
            if "error" in payload:
                print(f"  {name:<28} returned error: {payload['error']}")
                failures += 1
            else:
                print(f"  {name:<28} ok")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<28} FAILED: {exc.__class__.__name__}: {exc}")
            failures += 1

    print(f"\n  {len(ALL_TOOLS)} tools registered across {len(AGENT_TOOLS)} agents")

    print("\ngraph")
    try:
        nodes = sorted(build_graph().get_graph().nodes)
        print(f"  compiled with nodes: {', '.join(n for n in nodes if not n.startswith('__'))}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc.__class__.__name__}: {exc}")
        failures += 1

    print("\nconfiguration")
    for key, value in health().items():
        print(f"  {key:<18} {value}")
    if not health()["llm_configured"]:
        print("\n  note: GOOGLE_API_KEY is not set, so no agent can be invoked yet.")

    print()
    print("SMOKE TEST PASSED" if failures == 0 else f"SMOKE TEST FAILED ({failures} problem(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
