"""Execution of approved write actions.

The Recovery Agent can only *propose* changes. This module is the only place
that writes to NovaRetail's systems, and it is called exclusively by the
graph's approval node - after a human has approved.

Owner: Team Member 4 (Recovery & Supervisor Agent Engineer).
"""

from __future__ import annotations

from typing import Any

from .data import access
from .observability import traced


def describe(action: dict[str, Any]) -> str:
    """One-line, human-readable description of a proposed action."""
    kind = action.get("action")
    payload = action.get("payload", {})

    if kind == "create_incident":
        return (
            f"Create a {payload.get('severity', 'medium')} severity "
            f"{payload.get('type', 'other')} incident: "
            f"{payload.get('title', 'untitled')}"
        )
    if kind == "reroute_shipment":
        return (
            f"Reroute {payload.get('shipment_id')} from "
            f"{payload.get('from_route_id')} to {payload.get('to_route_id')} "
            f"({payload.get('reason', 'no reason given')})"
        )
    if kind == "escalate_incident":
        return (
            f"Escalate {payload.get('incident_id')} to "
            f"{payload.get('escalate_to', 'management')} "
            f"({payload.get('reason', 'no reason given')})"
        )
    return f"Unknown action: {kind}"


# The only write in the system, and the one step a reviewer most wants to find
# in a trace: what was applied, to what, and with whose approval.
@traced("apply approved action", run_type="tool")
def execute(action: dict[str, Any]) -> dict[str, Any]:
    """Apply an approved action. Returns a result record for the state."""
    kind = action.get("action")
    payload = dict(action.get("payload", {}))

    if kind == "create_incident":
        return _create_incident(payload)
    if kind == "reroute_shipment":
        return _reroute_shipment(payload)
    if kind == "escalate_incident":
        return _escalate_incident(payload)

    return {"action": kind, "ok": False, "result": f"unknown action {kind!r}"}


def _create_incident(payload: dict[str, Any]) -> dict[str, Any]:
    incident = {
        "incident_id": access.next_incident_id(),
        "type": payload.get("type", "other"),
        "severity": payload.get("severity", "medium"),
        "status": "open",
        "title": payload.get("title", "Untitled incident"),
        "description": payload.get("description", ""),
        "related_shipment_id": payload.get("related_shipment_id"),
        "related_supplier_id": payload.get("related_supplier_id"),
        "related_warehouse_id": payload.get("related_warehouse_id"),
        "related_skus": payload.get("related_skus") or [],
        "created_at": access.now_iso(),
        "owner": "central.supplychain@novaretail.example",
        "escalated": False,
        "created_by": "supplychain-assistant",
    }
    access.append_incident(incident)
    return {
        "action": "create_incident",
        "ok": True,
        "incident_id": incident["incident_id"],
        "result": f"Incident {incident['incident_id']} created ({incident['severity']}).",
        "record": incident,
    }


def _reroute_shipment(payload: dict[str, Any]) -> dict[str, Any]:
    shipment_id = payload.get("shipment_id", "")
    to_route_id = payload.get("to_route_id", "")
    route = access.get_route(to_route_id)
    if route is None:
        return {
            "action": "reroute_shipment",
            "ok": False,
            "result": f"Route {to_route_id} does not exist; nothing changed.",
        }

    updated = access.update_shipment(
        shipment_id,
        {
            "route_id": to_route_id,
            "mode": route["mode"],
            "origin": route["origin"],
            "status": "in_transit",
            "rerouted_from": payload.get("from_route_id"),
            "reroute_reason": payload.get("reason"),
        },
    )
    if updated is None:
        return {
            "action": "reroute_shipment",
            "ok": False,
            "result": f"Shipment {shipment_id} does not exist; nothing changed.",
        }

    return {
        "action": "reroute_shipment",
        "ok": True,
        "shipment_id": shipment_id,
        "result": (
            f"{shipment_id} rerouted onto {to_route_id} "
            f"({route['mode']}, {route['transit_days']} day transit)."
        ),
        "record": {
            "shipment_id": shipment_id,
            "route_id": to_route_id,
            "mode": route["mode"],
        },
    }


def _escalate_incident(payload: dict[str, Any]) -> dict[str, Any]:
    incident_id = payload.get("incident_id", "")
    incident = access.get_incident(incident_id)
    if incident is None:
        return {
            "action": "escalate_incident",
            "ok": False,
            "result": f"Incident {incident_id} does not exist; nothing changed.",
        }

    escalated = {
        **incident,
        "escalated": True,
        "escalated_to": payload.get("escalate_to", "VP Supply Chain"),
        "escalation_reason": payload.get("reason", ""),
        "escalated_at": access.now_iso(),
        "severity": (
            "critical" if incident.get("severity") == "high" else incident.get("severity")
        ),
    }
    access.upsert_incident(escalated)
    return {
        "action": "escalate_incident",
        "ok": True,
        "incident_id": incident_id,
        "result": (
            f"{incident_id} escalated to {escalated['escalated_to']} "
            f"(severity now {escalated['severity']})."
        ),
        "record": escalated,
    }
