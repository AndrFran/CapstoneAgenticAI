"""Supervisor Agent.

Receives every request, decides which supply chain function should handle it
next, and stops the workflow once the question is answered. It never calls
domain tools - its only output is a routing decision.

Owner: Team Member 4 (Recovery & Supervisor Agent Engineer).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import get_structured_llm
from ..prompts import SUPERVISOR_PROMPT
from ..state import RouteDecision

# Deterministic policy used when the routing LLM call fails, and as the
# tie-breaker documented in the architecture notes.
FALLBACK_FIRST_HOP = {
    "shipment_delay": "incident_analysis",
    "supplier_failure": "incident_analysis",
    "inventory_shortage": "inventory",
    "route_disruption": "incident_analysis",
    "damaged_goods": "incident_analysis",
    "status_query": "shipment",
    "other": "respond",
}

# After an agent reports, where the deterministic policy goes next.
FALLBACK_NEXT_HOP = {
    "incident_analysis": {
        "shipment_delay": "shipment",
        "supplier_failure": "supplier",
        "inventory_shortage": "inventory",
        "route_disruption": "shipment",
        "damaged_goods": "inventory",
    },
    "shipment": {"default": "recovery"},
    "inventory": {"default": "supplier"},
    "supplier": {"default": "recovery"},
    "recovery": {"default": "respond"},
}


def _fallback_decision(state: dict[str, Any]) -> RouteDecision:
    request = state.get("request") or {}
    incident_type = request.get("incident_type", "other")
    visited = state.get("visited") or []

    if not request.get("is_supported", True) or request.get("clarification_question"):
        return RouteDecision(
            next_agent="respond",
            reason="Request is unsupported or blocked on missing information.",
            task="Explain what is needed or why this cannot be handled.",
        )

    if not visited:
        target = FALLBACK_FIRST_HOP.get(incident_type, "respond")
    else:
        table = FALLBACK_NEXT_HOP.get(visited[-1], {})
        target = table.get(incident_type, table.get("default", "respond"))

    # Never send the same agent round twice under the fallback policy.
    if target in visited:
        target = "recovery" if "recovery" not in visited else "respond"

    return RouteDecision(
        next_agent=target,  # type: ignore[arg-type]
        reason="Deterministic routing policy (LLM routing unavailable).",
        task=request.get("intent", state.get("user_request", "")),
    )


def _state_digest(state: dict[str, Any]) -> str:
    request = state.get("request") or {}
    findings = state.get("findings") or {}
    lines = [
        f"User request: {state.get('user_request', '')}",
        "",
        "Intake:",
        f"  incident_type: {request.get('incident_type')}",
        f"  intent: {request.get('intent')}",
        f"  supported: {request.get('is_supported', True)}",
    ]
    for field in (
        "shipment_ids",
        "supplier_ids",
        "skus",
        "warehouse_ids",
        "order_ids",
        "incident_ids",
        "quantities",
    ):
        values = request.get(field) or []
        if values:
            lines.append(f"  {field}: {', '.join(str(v) for v in values)}")
    if request.get("missing_information"):
        lines.append("  missing_information: " + "; ".join(request["missing_information"]))

    lines.append("")
    lines.append(f"Agents already consulted: {', '.join(state.get('visited') or []) or 'none'}")
    if state.get("severity"):
        lines.append(f"Severity: {state['severity']}")
    if state.get("pending_action"):
        lines.append(
            "A write action is already awaiting approval; do not route to recovery again."
        )

    if findings:
        lines.append("")
        lines.append("Findings so far:")
        for agent, finding in findings.items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            lines.append(f"  [{agent}] {summary}")

    return "\n".join(lines)


def decide_route(state: dict[str, Any]) -> tuple[RouteDecision, bool]:
    """Pick the next agent. Returns the decision and whether the LLM produced it."""
    try:
        model = get_structured_llm("supervisor").with_structured_output(RouteDecision)
        decision = model.invoke(
            [
                SystemMessage(content=SUPERVISOR_PROMPT),
                HumanMessage(content=_state_digest(state)),
            ]
        )
        if isinstance(decision, dict):
            decision = RouteDecision(**decision)
        if isinstance(decision, RouteDecision):
            return decision, True
    except Exception:  # noqa: BLE001 - degrade to the deterministic policy
        pass

    return _fallback_decision(state), False
