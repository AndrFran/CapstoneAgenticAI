"""Incident analysis tools.

Backs the Incident Analysis Agent: pull the facts behind a disruption, assess
damaged goods, classify severity, and read the incident register.

Severity classification is deliberately rule-based rather than left to the
model, so the same disruption always scores the same way and the reasoning is
auditable in a LangSmith trace.

Owner: Team Member 1 (Request Intake & Incident Analysis Agent Engineer).
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..data import access
from . import shipment as shipment_tools
from .common import as_json, error, max_severity, normalise_id

INCIDENT_TYPES = (
    "shipment_delay",
    "supplier_failure",
    "inventory_shortage",
    "route_disruption",
    "damaged_goods",
    "other",
)


@tool
def get_shipment_details(shipment_id: str) -> str:
    """Pull the full record behind a shipment-related incident.

    Args:
        shipment_id: NovaRetail shipment id, e.g. "SHP-2026-0002".
    """
    return shipment_tools.track_shipment.invoke({"shipment_id": shipment_id})


@tool
def check_route_status(route_id: str) -> str:
    """Check whether a route is disrupted and what alternates exist.

    Args:
        route_id: Route id, e.g. "RTE-105".
    """
    return shipment_tools.check_delivery_route.invoke({"route_id": route_id})


@tool
def assess_damaged_goods(shipment_id: str) -> str:
    """Quantify damage on a shipment: units, value lost and salvage rate.

    Args:
        shipment_id: NovaRetail shipment id.
    """
    shipment_id = normalise_id(shipment_id)
    record = access.get_shipment(shipment_id)
    if record is None:
        return error(f"No shipment found with id {shipment_id!r}.")

    lines = []
    damaged_value = 0.0
    total_damaged = 0
    total_units = 0
    for item in record.get("line_items", []):
        product = access.get_product(item["sku"]) or {}
        damaged = item.get("damaged_quantity", 0)
        unit_cost = product.get("unit_cost", 0)
        damaged_value += damaged * unit_cost
        total_damaged += damaged
        total_units += item["quantity"]
        lines.append(
            {
                "sku": item["sku"],
                "product_name": product.get("name"),
                "shipped_quantity": item["quantity"],
                "damaged_quantity": damaged,
                "usable_quantity": item["quantity"] - damaged,
                "damage_rate": round(damaged / item["quantity"], 3)
                if item["quantity"]
                else 0,
                "value_lost_usd": round(damaged * unit_cost, 2),
            }
        )

    return as_json(
        {
            "shipment_id": shipment_id,
            "status": record["status"],
            "has_damage": total_damaged > 0,
            "total_units": total_units,
            "total_damaged_units": total_damaged,
            "overall_damage_rate": round(total_damaged / total_units, 3)
            if total_units
            else 0,
            "value_lost_usd": round(damaged_value, 2),
            "destination_warehouse_id": record["destination_warehouse_id"],
            "supplier_id": record["supplier_id"],
            "line_items": lines,
            "claim_recommended": damaged_value > 5000,
        }
    )


@tool
def find_related_incidents(
    shipment_id: str | None = None,
    supplier_id: str | None = None,
    sku: str | None = None,
    warehouse_id: str | None = None,
) -> str:
    """Find existing incidents already covering this situation.

    Call this before treating a disruption as new. If an open incident already
    covers the same shipment, supplier, SKU or warehouse, the operator needs its
    id - not a duplicate record.

    Args:
        shipment_id: Related shipment, if any.
        supplier_id: Related supplier, if any.
        sku: Related product, if any.
        warehouse_id: Related warehouse, if any.
    """
    shipment_id = normalise_id(shipment_id) or None
    supplier_id = normalise_id(supplier_id) or None
    sku = normalise_id(sku) or None
    warehouse_id = normalise_id(warehouse_id) or None

    if not any((shipment_id, supplier_id, sku, warehouse_id)):
        return error(
            "Provide at least one of shipment_id, supplier_id, sku or warehouse_id."
        )

    matches = []
    for incident in access.load("incidents"):
        reasons = []
        if shipment_id and incident.get("related_shipment_id") == shipment_id:
            reasons.append(f"same shipment {shipment_id}")
        if supplier_id and incident.get("related_supplier_id") == supplier_id:
            reasons.append(f"same supplier {supplier_id}")
        if warehouse_id and incident.get("related_warehouse_id") == warehouse_id:
            reasons.append(f"same warehouse {warehouse_id}")
        if sku and sku in (incident.get("related_skus") or []):
            reasons.append(f"same SKU {sku}")
        if not reasons:
            continue
        matches.append(
            {
                "incident_id": incident["incident_id"],
                "type": incident.get("type"),
                "severity": incident.get("severity"),
                "status": incident.get("status"),
                "title": incident.get("title"),
                "created_at": incident.get("created_at"),
                "escalated": incident.get("escalated", False),
                "matched_on": reasons,
                # Same shipment plus still open is the strong duplicate signal.
                "likely_duplicate": incident.get("status") == "open"
                and bool(shipment_id)
                and incident.get("related_shipment_id") == shipment_id,
            }
        )

    matches.sort(key=lambda m: (not m["likely_duplicate"], m.get("created_at") or ""))
    open_matches = [m for m in matches if m["status"] == "open"]

    return as_json(
        {
            "query": {
                "shipment_id": shipment_id,
                "supplier_id": supplier_id,
                "sku": sku,
                "warehouse_id": warehouse_id,
            },
            "match_count": len(matches),
            "open_match_count": len(open_matches),
            "duplicate_of": next(
                (m["incident_id"] for m in matches if m["likely_duplicate"]), None
            ),
            "matches": matches,
            "recommendation": (
                "An open incident already covers this - update it instead of "
                "raising a new one."
                if any(m["likely_duplicate"] for m in matches)
                else (
                    "Related history exists but nothing open on this exact "
                    "shipment; a new incident is reasonable."
                    if matches
                    else "Nothing on record - this is a new incident."
                )
            ),
        }
    )


def _order_impact(shipment_id: str) -> dict:
    """Order exposure behind a shipment: counts, units, stores and value."""
    orders = access.orders_for_shipment(shipment_id)
    at_risk = [o for o in orders if o["status"] == "at_risk"]
    value = 0.0
    for order in at_risk:
        product = access.get_product(order["sku"]) or {}
        value += order["quantity"] * product.get("retail_price", 0)
    return {
        "order_count": len(orders),
        "at_risk_count": len(at_risk),
        "at_risk_units": sum(o["quantity"] for o in at_risk),
        "at_risk_retail_value_usd": round(value, 2),
        "stores_affected": sorted({o["store_id"] for o in at_risk}),
    }


@tool
def classify_incident_severity(
    incident_type: str,
    shipment_id: str | None = None,
    sku: str | None = None,
    warehouse_id: str | None = None,
    supplier_id: str | None = None,
) -> str:
    """Score an incident's severity from the operational facts on record.

    Rules applied (highest wins):
      * critical - stockout with no cover network-wide, or delay over 72h on an
        expedited or high-value shipment
      * high     - delay over 48h, supplier suspended, below safety stock,
        damage on the load, or over $100k of at-risk order value
      * medium   - delay over 12h, route disrupted, below reorder point,
        3+ orders at risk, or 5+ stores exposed
      * low      - everything else

    Args:
        incident_type: One of shipment_delay, supplier_failure,
            inventory_shortage, route_disruption, damaged_goods, other.
        shipment_id: Related shipment, if any.
        sku: Related product, if any.
        warehouse_id: Related warehouse, if any.
        supplier_id: Related supplier, if any.
    """
    incident_type = (incident_type or "other").strip().lower()
    if incident_type not in INCIDENT_TYPES:
        return error(
            f"Unknown incident_type {incident_type!r}.",
            valid_incident_types=list(INCIDENT_TYPES),
        )

    signals: list[str] = []
    scores: list[str] = ["low"]

    shipment = access.get_shipment(normalise_id(shipment_id)) if shipment_id else None
    if shipment:
        delay = shipment.get("delay_hours", 0)
        value = shipment.get("value_usd", 0)
        expedited = shipment.get("priority") == "expedited"
        if delay > 72 and (expedited or value > 250_000):
            scores.append("critical")
            signals.append(
                f"{shipment['shipment_id']} is {delay}h late on a "
                f"{'expedited' if expedited else 'high-value'} load "
                f"(${value:,.0f})"
            )
        elif delay > 48:
            scores.append("high")
            signals.append(f"{shipment['shipment_id']} is {delay}h late")
        elif delay > 12:
            scores.append("medium")
            signals.append(f"{shipment['shipment_id']} is {delay}h late")
        if shipment["status"] == "damaged":
            scores.append("high")
            signals.append("damage reported against the load")

        route = access.get_route(shipment.get("route_id", "")) or {}
        if route.get("status") in {"disrupted", "weather_hold"}:
            scores.append("medium")
            signals.append(f"route {route['route_id']} is {route['status']}")

        # Downstream order exposure: how much customer promise is actually at
        # risk, not just how late the truck is.
        impact = _order_impact(shipment["shipment_id"])
        if impact["at_risk_retail_value_usd"] > 100_000:
            scores.append("high")
            signals.append(
                f"${impact['at_risk_retail_value_usd']:,.0f} of at-risk order value "
                f"across {impact['at_risk_count']} orders"
            )
        elif impact["at_risk_count"] >= 3:
            scores.append("medium")
            signals.append(f"{impact['at_risk_count']} orders at risk")
        if len(impact["stores_affected"]) >= 5:
            scores.append("medium")
            signals.append(f"{len(impact['stores_affected'])} stores exposed")

    if incident_type == "route_disruption" and not shipment:
        disrupted = [
            r["route_id"]
            for r in access.load("routes")
            if r["status"] in {"disrupted", "weather_hold"}
        ]
        if disrupted:
            scores.append("medium")
            signals.append(f"routes currently disrupted: {', '.join(disrupted)}")

    supplier = access.get_supplier(normalise_id(supplier_id)) if supplier_id else None
    if supplier is None and shipment:
        supplier = access.get_supplier(shipment["supplier_id"])
    if supplier:
        if supplier["status"] == "suspended":
            scores.append("high")
            signals.append(f"{supplier['supplier_id']} is suspended")
        elif supplier["status"] == "at_risk":
            scores.append("medium")
            signals.append(f"{supplier['supplier_id']} is flagged at_risk")

    if sku:
        sku_norm = normalise_id(sku)
        rows = access.inventory_for_sku(sku_norm)
        if warehouse_id:
            rows = [r for r in rows if r["warehouse_id"] == normalise_id(warehouse_id)]
        for row in rows:
            available = max(0, row["on_hand"] - row["reserved"])
            network_available = sum(
                max(0, r["on_hand"] - r["reserved"])
                for r in access.inventory_for_sku(sku_norm)
                if r["warehouse_id"] != row["warehouse_id"]
            )
            if available == 0 and network_available == 0:
                scores.append("critical")
                signals.append(f"{sku_norm} stocked out network-wide")
            elif available == 0:
                scores.append("high")
                signals.append(
                    f"{sku_norm} stocked out at {row['warehouse_id']} "
                    f"({network_available} units elsewhere)"
                )
            elif available < row["safety_stock"]:
                scores.append("high")
                signals.append(
                    f"{sku_norm} below safety stock at {row['warehouse_id']}"
                )
            elif available < row["reorder_point"]:
                scores.append("medium")
                signals.append(
                    f"{sku_norm} below reorder point at {row['warehouse_id']}"
                )

    severity = max_severity(*scores)
    if incident_type == "supplier_failure" and severity == "low":
        severity = "medium"
        signals.append("supplier failures are never lower than medium")

    return as_json(
        {
            "incident_type": incident_type,
            "severity": severity,
            "signals": signals or ["no elevated risk signals found on record"],
            "requires_escalation": severity in {"high", "critical"},
            "target_response_time_hours": {
                "critical": 1,
                "high": 4,
                "medium": 24,
                "low": 72,
            }[severity],
            "order_impact": (
                _order_impact(shipment["shipment_id"]) if shipment else None
            ),
            "inputs": {
                "shipment_id": normalise_id(shipment_id) or None,
                "sku": normalise_id(sku) or None,
                "warehouse_id": normalise_id(warehouse_id) or None,
                "supplier_id": normalise_id(supplier_id) or None,
            },
        }
    )


@tool
def check_incident_status(incident_id: str) -> str:
    """Look up an incident in the register.

    Args:
        incident_id: Incident id, e.g. "INC-2026-0001".
    """
    incident_id = normalise_id(incident_id)
    incident = access.get_incident(incident_id)
    if incident is None:
        return error(
            f"No incident found with id {incident_id!r}.",
            open_incident_ids=[
                inc["incident_id"]
                for inc in access.load("incidents")
                if inc.get("status") == "open"
            ],
        )
    return as_json(incident)


@tool
def list_open_incidents(severity: str | None = None) -> str:
    """List open incidents, optionally filtered by severity.

    Args:
        severity: Optional filter - low, medium, high or critical.
    """
    severity = (severity or "").strip().lower() or None
    incidents = [
        inc
        for inc in access.load("incidents")
        if inc.get("status") == "open"
        and (severity is None or inc.get("severity") == severity)
    ]
    incidents.sort(key=lambda inc: inc.get("created_at", ""), reverse=True)
    return as_json(
        {
            "filter_severity": severity,
            "open_count": len(incidents),
            "incidents": [
                {
                    "incident_id": inc["incident_id"],
                    "type": inc["type"],
                    "severity": inc["severity"],
                    "title": inc["title"],
                    "created_at": inc.get("created_at"),
                    "escalated": inc.get("escalated", False),
                }
                for inc in incidents
            ],
        }
    )


INCIDENT_TOOLS = [
    get_shipment_details,
    check_route_status,
    assess_damaged_goods,
    find_related_incidents,
    classify_incident_severity,
    check_incident_status,
    list_open_incidents,
]
