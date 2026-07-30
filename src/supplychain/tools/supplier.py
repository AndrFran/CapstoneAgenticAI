"""Supplier tools.

Backs the Supplier Agent: supplier lookup, availability, alternative sourcing,
side-by-side comparison and procurement cost estimates.

Owner: Team Member 3 (Inventory & Supplier Agent Engineer).
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..data import access
from .common import as_json, error, normalise_id

EXPEDITE_MULTIPLIER = 1.18
EXPEDITE_DAYS_SAVED = 3


def _supplier_view(supplier: dict, sku: str | None = None) -> dict:
    view = {
        "supplier_id": supplier["supplier_id"],
        "name": supplier["name"],
        "status": supplier["status"],
        "tier": supplier["tier"],
        "region": supplier["region"],
        "country": supplier["country"],
        "lead_time_days": supplier["lead_time_days"],
        "reliability_score": supplier["reliability_score"],
        "on_time_delivery_rate": supplier["on_time_delivery_rate"],
        "quality_score": supplier["quality_score"],
        "min_order_qty": supplier["min_order_qty"],
        "supplied_skus": supplier["supplied_skus"],
        "contact_email": supplier["contact_email"],
        "notes": supplier.get("notes") or None,
    }
    if sku:
        view["unit_price_for_sku"] = supplier.get("unit_prices", {}).get(sku)
    return view


@tool
def search_supplier(query: str) -> str:
    """Find suppliers by id, name, region, country or tier.

    Args:
        query: Free-text search term, e.g. "Vantage", "SUP-005" or "Bangladesh".
    """
    hits = access.search_suppliers(query)
    if not hits:
        return error(
            f"No suppliers matched {query!r}.",
            hint="Try a supplier id (SUP-005), a name fragment, or a region.",
        )
    return as_json(
        {
            "query": query,
            "match_count": len(hits),
            "suppliers": [_supplier_view(s) for s in hits],
        }
    )


@tool
def get_supplier_details(supplier_id: str) -> str:
    """Full profile for one supplier, including open shipments and incidents.

    Args:
        supplier_id: Supplier id, e.g. "SUP-005".
    """
    supplier_id = normalise_id(supplier_id)
    supplier = access.get_supplier(supplier_id)
    if supplier is None:
        return error(
            f"No supplier found with id {supplier_id!r}.",
            hint="Use search_supplier to look one up by name.",
        )

    open_shipments = [
        {
            "shipment_id": s["shipment_id"],
            "status": s["status"],
            "delay_hours": s.get("delay_hours", 0),
            "destination_warehouse_id": s["destination_warehouse_id"],
            "eta": s.get("eta"),
        }
        for s in access.find_all("shipments", supplier_id=supplier_id)
        if s["status"] != "delivered"
    ]
    related_incidents = [
        inc["incident_id"]
        for inc in access.load("incidents")
        if inc.get("related_supplier_id") == supplier_id
    ]

    return as_json(
        {
            **_supplier_view(supplier),
            "open_shipments": open_shipments,
            "open_shipment_count": len(open_shipments),
            "delayed_shipment_count": sum(
                1 for s in open_shipments if s["delay_hours"] > 0
            ),
            "related_incident_ids": related_incidents,
        }
    )


@tool
def check_supplier_availability(supplier_id: str, sku: str, quantity: int) -> str:
    """Can this supplier fulfil this quantity of this SKU, and by when?

    Args:
        supplier_id: Supplier id.
        sku: Product SKU.
        quantity: Units required.
    """
    supplier_id = normalise_id(supplier_id)
    sku = normalise_id(sku)
    supplier = access.get_supplier(supplier_id)
    if supplier is None:
        return error(f"No supplier found with id {supplier_id!r}.")
    if quantity <= 0:
        return error("quantity must be a positive number of units.")

    supplies_sku = sku in supplier["supplied_skus"]
    blocked = supplier["status"] == "suspended"
    meets_moq = quantity >= supplier["min_order_qty"]
    unit_price = supplier.get("unit_prices", {}).get(sku)

    reasons = []
    if not supplies_sku:
        reasons.append(f"{supplier_id} does not supply {sku}")
    if blocked:
        reasons.append(f"{supplier_id} is suspended: {supplier.get('notes')}")
    if not meets_moq:
        reasons.append(
            f"quantity {quantity} is below the minimum order of "
            f"{supplier['min_order_qty']}"
        )
    if supplier["status"] == "at_risk":
        reasons.append(
            f"{supplier_id} is flagged at_risk - confirm capacity before committing"
        )

    return as_json(
        {
            "supplier_id": supplier_id,
            "supplier_name": supplier["name"],
            "sku": sku,
            "quantity": quantity,
            "available": supplies_sku and not blocked and meets_moq,
            "supplier_status": supplier["status"],
            "supplies_sku": supplies_sku,
            "meets_minimum_order_qty": meets_moq,
            "lead_time_days": supplier["lead_time_days"],
            "expedited_lead_time_days": max(
                1, supplier["lead_time_days"] - EXPEDITE_DAYS_SAVED
            ),
            "unit_price_usd": unit_price,
            "estimated_cost_usd": (
                round(unit_price * quantity, 2) if unit_price else None
            ),
            "caveats": reasons,
        }
    )


@tool
def find_alternative_supplier(
    sku: str, exclude_supplier_id: str | None = None, quantity: int = 0
) -> str:
    """Find other suppliers who can provide a SKU, ranked by fit.

    Ranking favours active suppliers with short lead times and high
    reliability; suspended suppliers are excluded outright.

    Args:
        sku: Product SKU needed.
        exclude_supplier_id: Supplier to leave out (usually the failing one).
        quantity: Units required, used for cost and MOQ checks. 0 skips both.
    """
    sku = normalise_id(sku)
    exclude = normalise_id(exclude_supplier_id) or None
    product = access.get_product(sku)
    if product is None:
        return error(f"No product found with SKU {sku!r}.")

    candidates = []
    for supplier in access.suppliers_for_sku(sku):
        if supplier["supplier_id"] == exclude:
            continue
        if supplier["status"] == "suspended":
            continue
        unit_price = supplier.get("unit_prices", {}).get(sku)
        # Lower is better: lead time dominates, reliability breaks ties.
        score = supplier["lead_time_days"] * (2 - supplier["reliability_score"])
        candidates.append(
            {
                **_supplier_view(supplier, sku),
                "fit_score": round(score, 2),
                "meets_minimum_order_qty": (
                    quantity >= supplier["min_order_qty"] if quantity else None
                ),
                "estimated_cost_usd": (
                    round(unit_price * quantity, 2) if unit_price and quantity else None
                ),
            }
        )

    candidates.sort(key=lambda c: c["fit_score"])

    return as_json(
        {
            "sku": sku,
            "product_name": product["name"],
            "excluded_supplier_id": exclude,
            "quantity": quantity or None,
            "alternative_count": len(candidates),
            "recommended_supplier_id": (
                candidates[0]["supplier_id"] if candidates else None
            ),
            "alternatives": candidates,
            "note": (
                "No alternative supplier is active for this SKU - inventory "
                "transfer is the only near-term option."
                if not candidates
                else None
            ),
        }
    )


@tool
def compare_supplier_options(supplier_ids: list[str], sku: str, quantity: int) -> str:
    """Compare named suppliers side by side for a specific buy.

    Args:
        supplier_ids: Two or more supplier ids to compare.
        sku: Product SKU being sourced.
        quantity: Units required.
    """
    sku = normalise_id(sku)
    if not supplier_ids:
        return error("Provide at least one supplier id to compare.")
    if quantity <= 0:
        return error("quantity must be a positive number of units.")

    rows = []
    missing = []
    for raw_id in supplier_ids:
        supplier = access.get_supplier(normalise_id(raw_id))
        if supplier is None:
            missing.append(raw_id)
            continue
        unit_price = supplier.get("unit_prices", {}).get(sku)
        total = round(unit_price * quantity, 2) if unit_price else None
        rows.append(
            {
                "supplier_id": supplier["supplier_id"],
                "name": supplier["name"],
                "status": supplier["status"],
                "tier": supplier["tier"],
                "supplies_sku": sku in supplier["supplied_skus"],
                "lead_time_days": supplier["lead_time_days"],
                "reliability_score": supplier["reliability_score"],
                "on_time_delivery_rate": supplier["on_time_delivery_rate"],
                "unit_price_usd": unit_price,
                "total_cost_usd": total,
                "expedited_total_cost_usd": (
                    round(total * EXPEDITE_MULTIPLIER, 2) if total else None
                ),
                "meets_minimum_order_qty": quantity >= supplier["min_order_qty"],
            }
        )

    eligible = [r for r in rows if r["supplies_sku"] and r["status"] != "suspended"]
    cheapest = min(
        (r for r in eligible if r["total_cost_usd"]),
        key=lambda r: r["total_cost_usd"],
        default=None,
    )
    fastest = min(eligible, key=lambda r: r["lead_time_days"], default=None)
    most_reliable = max(eligible, key=lambda r: r["reliability_score"], default=None)

    return as_json(
        {
            "sku": sku,
            "quantity": quantity,
            "unknown_supplier_ids": missing,
            "comparison": rows,
            "cheapest_supplier_id": cheapest["supplier_id"] if cheapest else None,
            "fastest_supplier_id": fastest["supplier_id"] if fastest else None,
            "most_reliable_supplier_id": (
                most_reliable["supplier_id"] if most_reliable else None
            ),
        }
    )


@tool
def estimate_procurement_cost(
    supplier_id: str, sku: str, quantity: int, expedite: bool = False
) -> str:
    """Estimate the landed cost of buying a SKU from a supplier.

    Args:
        supplier_id: Supplier to buy from.
        sku: Product SKU.
        quantity: Units to buy.
        expedite: Whether to price the expedited freight option.
    """
    supplier_id = normalise_id(supplier_id)
    sku = normalise_id(sku)
    supplier = access.get_supplier(supplier_id)
    if supplier is None:
        return error(f"No supplier found with id {supplier_id!r}.")
    unit_price = supplier.get("unit_prices", {}).get(sku)
    if unit_price is None:
        return error(
            f"{supplier_id} has no price on file for {sku}.",
            supplied_skus=supplier["supplied_skus"],
        )
    if quantity <= 0:
        return error("quantity must be a positive number of units.")

    goods = round(unit_price * quantity, 2)
    freight = round(goods * (0.07 if not expedite else 0.07 * EXPEDITE_MULTIPLIER), 2)
    duty = round(goods * (0.0 if supplier["country"] == "USA" else 0.035), 2)
    total = round(goods + freight + duty, 2)
    lead_time = supplier["lead_time_days"] - (EXPEDITE_DAYS_SAVED if expedite else 0)

    return as_json(
        {
            "supplier_id": supplier_id,
            "supplier_name": supplier["name"],
            "sku": sku,
            "quantity": quantity,
            "expedite": expedite,
            "unit_price_usd": unit_price,
            "goods_cost_usd": goods,
            "freight_cost_usd": freight,
            "duty_cost_usd": duty,
            "total_landed_cost_usd": total,
            "landed_unit_cost_usd": round(total / quantity, 2),
            "lead_time_days": max(1, lead_time),
        }
    )


SUPPLIER_TOOLS = [
    search_supplier,
    get_supplier_details,
    check_supplier_availability,
    find_alternative_supplier,
    compare_supplier_options,
    estimate_procurement_cost,
]
