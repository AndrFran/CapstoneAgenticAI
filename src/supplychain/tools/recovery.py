"""Recovery and incident-action tools.

Backs the Recovery Agent: build a recovery plan, cost it, summarise it for
stakeholders, and *propose* the write actions that change NovaRetail's systems.

Write actions are proposals, not writes. ``propose_incident``,
``propose_reroute`` and ``propose_escalation`` return the exact action they
would take; the graph's approval node is what executes it after a human says
yes (see ``supplychain.actions``). That keeps the human-in-the-loop gate out of
the model's hands - it cannot decide to skip the confirmation.

Owner: Team Member 4 (Recovery & Supervisor Agent Engineer).
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..data import access
from .common import as_json, error, normalise_id

# Names the graph looks for when scanning an agent's tool calls for a pending
# write action. Keep in sync with the @tool functions below.
PROPOSAL_TOOL_NAMES = ("propose_incident", "propose_reroute", "propose_escalation")

EXPEDITE_FREIGHT_PREMIUM_USD_PER_UNIT = 1.85
AIR_UPGRADE_PREMIUM_USD_PER_UNIT = 4.40
STOCKOUT_COST_PER_UNIT_PER_DAY = 2.30


@tool
def generate_recovery_plan(
    incident_type: str,
    shipment_id: str | None = None,
    sku: str | None = None,
    warehouse_id: str | None = None,
    supplier_id: str | None = None,
    required_units: int = 0,
) -> str:
    """Assemble ranked recovery options for a disruption.

    Reads the live position (shipment, route, inventory, suppliers) and returns
    concrete options with lead times, costs and trade-offs - reroute, transfer,
    alternative sourcing, expedite, or accept-and-communicate.

    Args:
        incident_type: shipment_delay, supplier_failure, inventory_shortage,
            route_disruption, damaged_goods or other.
        shipment_id: Related shipment, if any.
        sku: Product at risk, if any.
        warehouse_id: Warehouse affected, if any.
        supplier_id: Supplier involved, if any.
        required_units: Units that need covering. 0 if not yet known.
    """
    incident_type = (incident_type or "other").strip().lower()
    shipment = access.get_shipment(normalise_id(shipment_id)) if shipment_id else None
    sku = normalise_id(sku) or None
    warehouse_id = normalise_id(warehouse_id) or None
    supplier_id = normalise_id(supplier_id) or (
        shipment["supplier_id"] if shipment else None
    )

    if shipment and not warehouse_id:
        warehouse_id = shipment["destination_warehouse_id"]
    if shipment and not sku and shipment.get("line_items"):
        sku = shipment["line_items"][0]["sku"]

    options: list[dict] = []

    # --- Option 1: reroute a shipment that is stuck on a bad route ----------
    if shipment:
        route = access.get_route(shipment.get("route_id", "")) or {}
        for alt_id in route.get("alternate_route_ids", []):
            alt = access.get_route(alt_id)
            if not alt or alt["status"] not in {"clear", "congested"}:
                continue
            saved_days = max(0, route.get("transit_days", 0) - alt["transit_days"])
            units = sum(i["quantity"] for i in shipment.get("line_items", []))
            premium = (
                AIR_UPGRADE_PREMIUM_USD_PER_UNIT
                if alt["mode"] == "air"
                else EXPEDITE_FREIGHT_PREMIUM_USD_PER_UNIT
            )
            options.append(
                {
                    "option": "reroute_shipment",
                    "action": f"Move {shipment['shipment_id']} from {route.get('route_id')} to {alt_id}",
                    "shipment_id": shipment["shipment_id"],
                    "from_route_id": route.get("route_id"),
                    "to_route_id": alt_id,
                    "mode": alt["mode"],
                    "days_recovered": saved_days,
                    "estimated_cost_usd": round(units * premium, 2),
                    "confidence": "high" if alt["status"] == "clear" else "medium",
                    "next_step": "propose_reroute",
                }
            )

    # --- Option 2: inter-warehouse transfer --------------------------------
    if sku and warehouse_id and required_units > 0:
        donors = []
        for row in access.inventory_for_sku(sku):
            if row["warehouse_id"] == warehouse_id:
                continue
            spare = max(0, row["on_hand"] - row["reserved"] - row["safety_stock"])
            if spare > 0:
                donors.append((row["warehouse_id"], spare))
        donors.sort(key=lambda d: d[1], reverse=True)
        coverable = min(required_units, sum(spare for _, spare in donors))
        if coverable > 0:
            options.append(
                {
                    "option": "inventory_transfer",
                    "action": (
                        f"Transfer {coverable} units of {sku} into {warehouse_id} "
                        f"from {', '.join(w for w, _ in donors[:3])}"
                    ),
                    "sku": sku,
                    "to_warehouse_id": warehouse_id,
                    "units_covered": coverable,
                    "donor_warehouses": [w for w, _ in donors[:3]],
                    "lead_time_days": 2,
                    "estimated_cost_usd": round(coverable * 0.62, 2),
                    "confidence": "high",
                    "next_step": "no approval required - raise a transfer order",
                }
            )

    # --- Option 3: alternative supplier ------------------------------------
    if sku:
        alternatives = [
            s
            for s in access.suppliers_for_sku(sku)
            if s["status"] != "suspended" and s["supplier_id"] != supplier_id
        ]
        alternatives.sort(
            key=lambda s: s["lead_time_days"] * (2 - s["reliability_score"])
        )
        if alternatives:
            best = alternatives[0]
            unit_price = best.get("unit_prices", {}).get(sku)
            qty = required_units or best["min_order_qty"]
            options.append(
                {
                    "option": "alternative_supplier",
                    "action": (
                        f"Source {qty} units of {sku} from {best['supplier_id']} "
                        f"({best['name']})"
                    ),
                    "sku": sku,
                    "supplier_id": best["supplier_id"],
                    "supplier_name": best["name"],
                    "units": qty,
                    "lead_time_days": best["lead_time_days"],
                    "reliability_score": best["reliability_score"],
                    "estimated_cost_usd": (
                        round(unit_price * qty, 2) if unit_price else None
                    ),
                    "confidence": "medium",
                    "next_step": "raise a purchase order once approved",
                }
            )

    # --- Option 4: always available fallback -------------------------------
    options.append(
        {
            "option": "accept_and_communicate",
            "action": (
                "Accept the delay, notify affected stores and customers, and "
                "reprioritise allocation to the highest-value orders"
            ),
            "lead_time_days": 0,
            "estimated_cost_usd": 0.0,
            "confidence": "high",
            "next_step": "generate_incident_summary and notify stakeholders",
        }
    )

    ranked = sorted(
        options,
        key=lambda o: (
            o.get("lead_time_days", 99),
            o.get("estimated_cost_usd") or 0,
        ),
    )

    return as_json(
        {
            "incident_type": incident_type,
            "context": {
                "shipment_id": shipment["shipment_id"] if shipment else None,
                "sku": sku,
                "warehouse_id": warehouse_id,
                "supplier_id": supplier_id,
                "required_units": required_units or None,
            },
            "option_count": len(ranked),
            "recommended_option": ranked[0]["option"] if ranked else None,
            "options": ranked,
            "requires_incident_record": incident_type != "other",
        }
    )


@tool
def estimate_recovery_cost(
    option: str,
    units: int,
    delay_days: int = 0,
    unit_cost_usd: float = 0.0,
) -> str:
    """Cost a recovery option against the cost of doing nothing.

    Args:
        option: One of reroute_shipment, inventory_transfer,
            alternative_supplier, expedite_freight, accept_and_communicate.
        units: Units involved.
        delay_days: Days of delay being avoided (drives the do-nothing cost).
        unit_cost_usd: Product unit cost, used for expedite and sourcing maths.
    """
    option = (option or "").strip().lower()
    if units <= 0:
        return error("units must be a positive number.")

    per_unit = {
        "reroute_shipment": EXPEDITE_FREIGHT_PREMIUM_USD_PER_UNIT,
        "inventory_transfer": 0.62,
        "alternative_supplier": max(unit_cost_usd * 0.06, 1.10),
        "expedite_freight": AIR_UPGRADE_PREMIUM_USD_PER_UNIT,
        "accept_and_communicate": 0.0,
    }
    if option not in per_unit:
        return error(
            f"Unknown recovery option {option!r}.",
            valid_options=list(per_unit),
        )

    recovery_cost = round(units * per_unit[option], 2)
    do_nothing_cost = round(units * STOCKOUT_COST_PER_UNIT_PER_DAY * delay_days, 2)

    return as_json(
        {
            "option": option,
            "units": units,
            "delay_days_avoided": delay_days,
            "recovery_cost_usd": recovery_cost,
            "cost_of_inaction_usd": do_nothing_cost,
            "net_benefit_usd": round(do_nothing_cost - recovery_cost, 2),
            "worth_doing": do_nothing_cost > recovery_cost,
            "assumptions": {
                "cost_per_unit_usd": per_unit[option],
                "stockout_cost_per_unit_per_day_usd": STOCKOUT_COST_PER_UNIT_PER_DAY,
            },
        }
    )


@tool
def generate_incident_summary(
    incident_type: str,
    severity: str,
    headline: str,
    impact: str,
    recommended_action: str,
    incident_id: str | None = None,
) -> str:
    """Produce the stakeholder summary for an incident.

    Args:
        incident_type: Incident type.
        severity: low, medium, high or critical.
        headline: One-line description of what happened.
        impact: What it affects - orders, stores, revenue, SKUs.
        recommended_action: The recommended recovery action.
        incident_id: Existing incident id, if one has been raised.
    """
    severity = (severity or "medium").strip().lower()
    audience = {
        "critical": ["COO", "VP Supply Chain", "Regional Ops Managers"],
        "high": ["VP Supply Chain", "Regional Ops Managers"],
        "medium": ["Central Supply Chain Team"],
        "low": ["Central Supply Chain Team"],
    }.get(severity, ["Central Supply Chain Team"])

    return as_json(
        {
            "incident_id": normalise_id(incident_id) or None,
            "incident_type": incident_type,
            "severity": severity,
            "headline": headline,
            "impact": impact,
            "recommended_action": recommended_action,
            "distribution_list": audience,
            "summary_markdown": (
                f"**{severity.upper()} - {headline}**\n\n"
                f"- **Impact:** {impact}\n"
                f"- **Recommended action:** {recommended_action}\n"
                f"- **Incident:** {normalise_id(incident_id) or 'not yet raised'}\n"
                f"- **Notify:** {', '.join(audience)}"
            ),
        }
    )


# ---------------------------------------------------------------------------
# Write-action proposals (executed by the approval node, not here)
# ---------------------------------------------------------------------------


@tool
def propose_incident(
    incident_type: str,
    severity: str,
    title: str,
    description: str,
    related_shipment_id: str | None = None,
    related_supplier_id: str | None = None,
    related_warehouse_id: str | None = None,
    related_skus: list[str] | None = None,
) -> str:
    """Propose creating a supply chain incident record. Needs human approval.

    Nothing is written by this call. The proposal is surfaced to the operator,
    and only executed if they approve.

    Args:
        incident_type: shipment_delay, supplier_failure, inventory_shortage,
            route_disruption, damaged_goods or other.
        severity: low, medium, high or critical.
        title: Short incident title.
        description: What happened and what the impact is.
        related_shipment_id: Related shipment, if any.
        related_supplier_id: Related supplier, if any.
        related_warehouse_id: Related warehouse, if any.
        related_skus: Related SKUs, if any.
    """
    return as_json(
        {
            "proposed_action": "create_incident",
            "status": "awaiting_human_approval",
            "payload": {
                "type": (incident_type or "other").strip().lower(),
                "severity": (severity or "medium").strip().lower(),
                "title": title,
                "description": description,
                "related_shipment_id": normalise_id(related_shipment_id) or None,
                "related_supplier_id": normalise_id(related_supplier_id) or None,
                "related_warehouse_id": normalise_id(related_warehouse_id) or None,
                "related_skus": [normalise_id(s) for s in (related_skus or [])],
            },
            "note": (
                "Proposal recorded. Tell the user what you intend to create and "
                "that it needs their approval - do not claim it exists yet."
            ),
        }
    )


@tool
def propose_reroute(shipment_id: str, to_route_id: str, reason: str) -> str:
    """Propose rerouting a shipment onto an alternate route. Needs approval.

    Args:
        shipment_id: Shipment to reroute.
        to_route_id: Target route id.
        reason: Why the reroute is needed.
    """
    shipment_id = normalise_id(shipment_id)
    to_route_id = normalise_id(to_route_id)

    shipment = access.get_shipment(shipment_id)
    if shipment is None:
        return error(f"No shipment found with id {shipment_id!r}.")
    route = access.get_route(to_route_id)
    if route is None:
        return error(f"No route found with id {to_route_id!r}.")
    if route["destination_warehouse_id"] != shipment["destination_warehouse_id"]:
        return error(
            f"{to_route_id} does not serve {shipment['destination_warehouse_id']}.",
            route_destination=route["destination_warehouse_id"],
        )

    return as_json(
        {
            "proposed_action": "reroute_shipment",
            "status": "awaiting_human_approval",
            "payload": {
                "shipment_id": shipment_id,
                "from_route_id": shipment.get("route_id"),
                "to_route_id": to_route_id,
                "new_mode": route["mode"],
                "new_transit_days": route["transit_days"],
                "reason": reason,
            },
            "note": "Proposal recorded; awaiting operator approval.",
        }
    )


@tool
def propose_escalation(incident_id: str, reason: str, escalate_to: str) -> str:
    """Propose escalating an existing incident. Needs human approval.

    Args:
        incident_id: Incident to escalate.
        reason: Why escalation is warranted.
        escalate_to: Role or team to escalate to, e.g. "VP Supply Chain".
    """
    incident_id = normalise_id(incident_id)
    incident = access.get_incident(incident_id)
    if incident is None:
        return error(
            f"No incident found with id {incident_id!r}.",
            hint="Raise the incident first with propose_incident.",
        )

    return as_json(
        {
            "proposed_action": "escalate_incident",
            "status": "awaiting_human_approval",
            "payload": {
                "incident_id": incident_id,
                "current_severity": incident.get("severity"),
                "reason": reason,
                "escalate_to": escalate_to,
            },
            "note": "Proposal recorded; awaiting operator approval.",
        }
    )


RECOVERY_TOOLS = [
    generate_recovery_plan,
    estimate_recovery_cost,
    generate_incident_summary,
    propose_incident,
    propose_reroute,
    propose_escalation,
]
