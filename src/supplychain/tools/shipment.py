"""Shipment and order tools.

Backs the Shipment Agent: track shipments, quantify delays, find the orders a
disruption puts at risk, and check delivery routes.

Owner: Team Member 2 (Shipment & Order Impact Agent Engineer).
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..data import access
from .common import as_json, days_until, error, normalise_id

DELAY_STATUSES = {"delayed", "damaged", "at_customs"}


def _shipment_summary(shipment: dict) -> dict:
    warehouse = access.get_warehouse(shipment["destination_warehouse_id"]) or {}
    supplier = access.get_supplier(shipment["supplier_id"]) or {}
    return {
        "shipment_id": shipment["shipment_id"],
        "status": shipment["status"],
        "carrier": shipment.get("carrier"),
        "mode": shipment.get("mode"),
        "origin": shipment.get("origin"),
        "destination": {
            "warehouse_id": shipment["destination_warehouse_id"],
            "name": warehouse.get("name"),
            "region": warehouse.get("region"),
        },
        "supplier": {
            "supplier_id": shipment["supplier_id"],
            "name": supplier.get("name"),
            "status": supplier.get("status"),
        },
        "route_id": shipment.get("route_id"),
        "departed_at": shipment.get("departed_at"),
        "original_eta": shipment.get("original_eta"),
        "eta": shipment.get("eta"),
        "delay_hours": shipment.get("delay_hours", 0),
        "days_to_eta": days_until(shipment.get("eta")),
        "last_scan": {
            "location": shipment.get("last_scan_location"),
            "at": shipment.get("last_scan_at"),
        },
        "line_items": shipment.get("line_items", []),
        "value_usd": shipment.get("value_usd"),
        "priority": shipment.get("priority"),
    }


@tool
def track_shipment(shipment_id: str) -> str:
    """Track a shipment end to end: status, ETA, last scan, contents and value.

    Args:
        shipment_id: NovaRetail shipment id, e.g. "SHP-2026-0001".
    """
    shipment = access.get_shipment(normalise_id(shipment_id))
    if shipment is None:
        return error(
            f"No shipment found with id {shipment_id!r}.",
            hint="Shipment ids look like SHP-2026-0001.",
            sample_ids=[s["shipment_id"] for s in access.load("shipments")[:5]],
        )
    return as_json(_shipment_summary(shipment))


@tool
def get_shipment_status(shipment_id: str) -> str:
    """Return just the current status and ETA for a shipment (cheap check).

    Args:
        shipment_id: NovaRetail shipment id.
    """
    shipment = access.get_shipment(normalise_id(shipment_id))
    if shipment is None:
        return error(f"No shipment found with id {shipment_id!r}.")
    return as_json(
        {
            "shipment_id": shipment["shipment_id"],
            "status": shipment["status"],
            "eta": shipment.get("eta"),
            "original_eta": shipment.get("original_eta"),
            "delay_hours": shipment.get("delay_hours", 0),
            "is_delayed": shipment["status"] in DELAY_STATUSES
            or shipment.get("delay_hours", 0) > 0,
        }
    )


@tool
def check_shipment_delay(shipment_id: str) -> str:
    """Explain whether a shipment is delayed, by how much, and why.

    Args:
        shipment_id: NovaRetail shipment id.
    """
    shipment = access.get_shipment(normalise_id(shipment_id))
    if shipment is None:
        return error(f"No shipment found with id {shipment_id!r}.")

    route = access.get_route(shipment.get("route_id", "")) or {}
    delay_hours = shipment.get("delay_hours", 0)
    causes = []
    if route.get("disruption"):
        causes.append(f"route {route['route_id']}: {route['disruption']}")
    if shipment["status"] == "at_customs":
        causes.append("held in customs clearance")
    if shipment["status"] == "damaged":
        causes.append("damage reported on part of the load")
    supplier = access.get_supplier(shipment["supplier_id"]) or {}
    if supplier.get("status") in {"at_risk", "suspended"}:
        causes.append(f"supplier {supplier['supplier_id']} is {supplier['status']}")

    return as_json(
        {
            "shipment_id": shipment["shipment_id"],
            "status": shipment["status"],
            "is_delayed": delay_hours > 0 or shipment["status"] in DELAY_STATUSES,
            "delay_hours": delay_hours,
            "delay_days": round(delay_hours / 24, 1),
            "original_eta": shipment.get("original_eta"),
            "revised_eta": shipment.get("eta"),
            "likely_causes": causes or ["no disruption signal on record"],
            "route_status": route.get("status"),
        }
    )


@tool
def estimate_delivery_delay(shipment_id: str) -> str:
    """Estimate the downstream delivery impact of a shipment's delay.

    Combines the shipment's delay with route status to project a revised
    warehouse receipt date and a store-availability date.

    Args:
        shipment_id: NovaRetail shipment id.
    """
    shipment = access.get_shipment(normalise_id(shipment_id))
    if shipment is None:
        return error(f"No shipment found with id {shipment_id!r}.")

    route = access.get_route(shipment.get("route_id", "")) or {}
    base_delay = shipment.get("delay_hours", 0)
    route_penalty = {
        "clear": 0,
        "congested": 18,
        "disrupted": 30,
        "weather_hold": 24,
    }.get(route.get("status", "clear"), 0)

    projected_delay = base_delay + route_penalty
    # Cross-dock plus store transfer is a further 24h in NovaRetail's network.
    store_availability_delay = projected_delay + 24

    return as_json(
        {
            "shipment_id": shipment["shipment_id"],
            "recorded_delay_hours": base_delay,
            "route_status": route.get("status"),
            "additional_route_risk_hours": route_penalty,
            "projected_delay_hours": projected_delay,
            "projected_delay_days": round(projected_delay / 24, 1),
            "warehouse_receipt_eta": shipment.get("eta"),
            "store_availability_delay_hours": store_availability_delay,
            "confidence": "medium" if route_penalty else "high",
        }
    )


@tool
def find_affected_orders(shipment_id: str) -> str:
    """List the customer and replenishment orders riding on a shipment.

    Args:
        shipment_id: NovaRetail shipment id.
    """
    shipment_id = normalise_id(shipment_id)
    shipment = access.get_shipment(shipment_id)
    if shipment is None:
        return error(f"No shipment found with id {shipment_id!r}.")

    orders = access.orders_for_shipment(shipment_id)
    at_risk = [o for o in orders if o["status"] == "at_risk"]
    units = sum(o["quantity"] for o in orders)

    return as_json(
        {
            "shipment_id": shipment_id,
            "shipment_status": shipment["status"],
            "order_count": len(orders),
            "at_risk_count": len(at_risk),
            "total_units": units,
            "stores_affected": sorted({o["store_id"] for o in orders}),
            "channels": sorted({o["channel"] for o in orders}),
            "orders": [
                {
                    "order_id": o["order_id"],
                    "store_id": o["store_id"],
                    "sku": o["sku"],
                    "quantity": o["quantity"],
                    "promised_date": o["promised_date"],
                    "status": o["status"],
                    "channel": o["channel"],
                }
                for o in orders
            ],
        }
    )


@tool
def identify_delayed_shipments(warehouse_id: str | None = None) -> str:
    """List every currently delayed shipment, optionally for one warehouse.

    Args:
        warehouse_id: Optional destination warehouse filter, e.g. "WH-N04".
    """
    warehouse_id = normalise_id(warehouse_id) or None
    delayed = [
        s
        for s in access.load("shipments")
        if (s["status"] in DELAY_STATUSES or s.get("delay_hours", 0) > 0)
        and (warehouse_id is None or s["destination_warehouse_id"] == warehouse_id)
    ]
    delayed.sort(key=lambda s: s.get("delay_hours", 0), reverse=True)

    return as_json(
        {
            "filter_warehouse_id": warehouse_id,
            "delayed_count": len(delayed),
            "shipments": [
                {
                    "shipment_id": s["shipment_id"],
                    "status": s["status"],
                    "delay_hours": s.get("delay_hours", 0),
                    "destination_warehouse_id": s["destination_warehouse_id"],
                    "supplier_id": s["supplier_id"],
                    "eta": s.get("eta"),
                    "value_usd": s.get("value_usd"),
                }
                for s in delayed
            ],
        }
    )


@tool
def check_delivery_route(route_id: str) -> str:
    """Check a delivery route's status, disruptions and alternates.

    Args:
        route_id: Route id, e.g. "RTE-102".
    """
    route = access.get_route(normalise_id(route_id))
    if route is None:
        return error(
            f"No route found with id {route_id!r}.",
            available_route_ids=[r["route_id"] for r in access.load("routes")],
        )

    alternates = []
    for alt_id in route.get("alternate_route_ids", []):
        alt = access.get_route(alt_id)
        if alt:
            alternates.append(
                {
                    "route_id": alt["route_id"],
                    "status": alt["status"],
                    "mode": alt["mode"],
                    "transit_days": alt["transit_days"],
                    "origin": alt["origin"],
                }
            )

    in_flight = [
        s["shipment_id"]
        for s in access.load("shipments")
        if s.get("route_id") == route["route_id"] and s["status"] != "delivered"
    ]

    return as_json(
        {
            "route_id": route["route_id"],
            "origin": route["origin"],
            "destination_warehouse_id": route["destination_warehouse_id"],
            "mode": route["mode"],
            "transit_days": route["transit_days"],
            "status": route["status"],
            "disruption": route.get("disruption"),
            "alternates": alternates,
            "shipments_in_flight": in_flight,
            "last_updated": route.get("last_updated"),
        }
    )


SHIPMENT_TOOLS = [
    track_shipment,
    get_shipment_status,
    check_shipment_delay,
    estimate_delivery_delay,
    find_affected_orders,
    identify_delayed_shipments,
    check_delivery_route,
]
