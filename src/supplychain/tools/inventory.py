"""Inventory tools.

Backs the Inventory Agent: stock positions, shortage detection, required
quantities and inter-warehouse transfer options.

Owner: Team Member 3 (Inventory & Supplier Agent Engineer).
"""

from __future__ import annotations

from langchain_core.tools import tool

from ..data import access
from .common import as_json, error, normalise_id

# A warehouse is a viable donor only if it can give stock away without
# dropping below its own safety stock.
TRANSFER_LEAD_TIME_DAYS = {
    ("Northeast", "Southeast"): 2,
    ("Southeast", "Northeast"): 2,
    ("Midwest", "Northeast"): 2,
    ("Midwest", "Southwest"): 2,
    ("Southwest", "West"): 2,
    ("West", "Southwest"): 2,
    ("West", "Northwest"): 1,
    ("Northwest", "West"): 1,
}
DEFAULT_TRANSFER_DAYS = 3
TRANSFER_COST_PER_UNIT = 0.62


def _available(row: dict) -> int:
    return max(0, row["on_hand"] - row["reserved"])


def _position(row: dict) -> dict:
    warehouse = access.get_warehouse(row["warehouse_id"]) or {}
    available = _available(row)
    demand = row["avg_daily_demand"] or 1
    return {
        "warehouse_id": row["warehouse_id"],
        "warehouse_name": warehouse.get("name"),
        "region": warehouse.get("region"),
        "sku": row["sku"],
        "on_hand": row["on_hand"],
        "reserved": row["reserved"],
        "available": available,
        "in_transit": row.get("in_transit", 0),
        "reorder_point": row["reorder_point"],
        "safety_stock": row["safety_stock"],
        "avg_daily_demand": row["avg_daily_demand"],
        "days_of_cover": round(available / demand, 1),
        "below_reorder_point": available < row["reorder_point"],
        "below_safety_stock": available < row["safety_stock"],
        "stockout": available == 0,
        "last_counted": row.get("last_counted"),
    }


@tool
def check_inventory(sku: str) -> str:
    """Network-wide stock position for one product, warehouse by warehouse.

    Args:
        sku: Product SKU, e.g. "SKU-1001".
    """
    sku = normalise_id(sku)
    product = access.get_product(sku)
    if product is None:
        return error(
            f"No product found with SKU {sku!r}.",
            hint="SKUs look like SKU-1001.",
            sample_skus=[p["sku"] for p in access.load("products")[:6]],
        )

    rows = [_position(row) for row in access.inventory_for_sku(sku)]
    rows.sort(key=lambda r: r["available"], reverse=True)
    total_available = sum(r["available"] for r in rows)

    return as_json(
        {
            "sku": sku,
            "product_name": product["name"],
            "category": product["category"],
            "unit_cost": product["unit_cost"],
            "total_on_hand": sum(r["on_hand"] for r in rows),
            "total_available": total_available,
            "total_in_transit": sum(r["in_transit"] for r in rows),
            "warehouses_below_reorder_point": [
                r["warehouse_id"] for r in rows if r["below_reorder_point"]
            ],
            "warehouses_stocked_out": [r["warehouse_id"] for r in rows if r["stockout"]],
            "positions": rows,
        }
    )


@tool
def check_warehouse_stock(warehouse_id: str, sku: str) -> str:
    """Stock position for one SKU at one warehouse.

    Args:
        warehouse_id: Warehouse id, e.g. "WH-N04".
        sku: Product SKU, e.g. "SKU-1001".
    """
    warehouse_id = normalise_id(warehouse_id)
    sku = normalise_id(sku)
    row = access.get_inventory_row(warehouse_id, sku)
    if row is None:
        return error(
            f"No inventory record for {sku} at {warehouse_id}.",
            known_warehouse_ids=[w["warehouse_id"] for w in access.load("warehouses")],
        )
    return as_json(_position(row))


@tool
def check_warehouse_availability(warehouse_id: str) -> str:
    """Summarise a warehouse: what is short, what is healthy, worst offenders.

    Args:
        warehouse_id: Warehouse id, e.g. "WH-N02".
    """
    warehouse_id = normalise_id(warehouse_id)
    warehouse = access.get_warehouse(warehouse_id)
    if warehouse is None:
        return error(
            f"No warehouse found with id {warehouse_id!r}.",
            known_warehouse_ids=[w["warehouse_id"] for w in access.load("warehouses")],
        )

    rows = [
        _position(row)
        for row in access.load("inventory")
        if row["warehouse_id"] == warehouse_id
    ]
    short = [r for r in rows if r["below_reorder_point"]]
    short.sort(key=lambda r: r["days_of_cover"])

    return as_json(
        {
            "warehouse_id": warehouse_id,
            "name": warehouse["name"],
            "region": warehouse["region"],
            "location": warehouse["location"],
            "skus_tracked": len(rows),
            "skus_below_reorder_point": len(short),
            "skus_stocked_out": sum(1 for r in rows if r["stockout"]),
            "shortages": [
                {
                    "sku": r["sku"],
                    "available": r["available"],
                    "reorder_point": r["reorder_point"],
                    "days_of_cover": r["days_of_cover"],
                }
                for r in short[:10]
            ],
        }
    )


@tool
def identify_inventory_shortages(
    sku: str | None = None, warehouse_id: str | None = None
) -> str:
    """Find every stock position below its reorder point.

    Args:
        sku: Optional SKU filter.
        warehouse_id: Optional warehouse filter.
    """
    sku = normalise_id(sku) or None
    warehouse_id = normalise_id(warehouse_id) or None

    rows = [
        _position(row)
        for row in access.load("inventory")
        if (sku is None or row["sku"] == sku)
        and (warehouse_id is None or row["warehouse_id"] == warehouse_id)
    ]
    short = [r for r in rows if r["below_reorder_point"]]
    short.sort(key=lambda r: r["days_of_cover"])

    return as_json(
        {
            "filters": {"sku": sku, "warehouse_id": warehouse_id},
            "shortage_count": len(short),
            "critical_count": sum(1 for r in short if r["stockout"]),
            "shortages": short[:25],
        }
    )


@tool
def calculate_required_quantity(
    sku: str, warehouse_id: str, cover_days: int = 14
) -> str:
    """Work out how many units are needed to restore a target days-of-cover.

    Args:
        sku: Product SKU.
        warehouse_id: Warehouse to replenish.
        cover_days: Target days of demand cover (default 14).
    """
    sku = normalise_id(sku)
    warehouse_id = normalise_id(warehouse_id)
    row = access.get_inventory_row(warehouse_id, sku)
    if row is None:
        return error(f"No inventory record for {sku} at {warehouse_id}.")

    product = access.get_product(sku) or {}
    available = _available(row)
    target = row["avg_daily_demand"] * max(1, cover_days)
    gap = max(0, target - available - row.get("in_transit", 0))

    return as_json(
        {
            "sku": sku,
            "warehouse_id": warehouse_id,
            "available": available,
            "in_transit": row.get("in_transit", 0),
            "avg_daily_demand": row["avg_daily_demand"],
            "target_cover_days": cover_days,
            "target_units": target,
            "required_units": gap,
            "shortfall_vs_safety_stock": max(0, row["safety_stock"] - available),
            "estimated_purchase_cost_usd": round(gap * product.get("unit_cost", 0), 2),
        }
    )


@tool
def find_inventory_transfer(sku: str, warehouse_id: str, quantity: int) -> str:
    """Find warehouses that can transfer stock to cover a shortage.

    Only offers stock a donor can release while staying above its own safety
    stock, so the recommendation does not simply move the stockout elsewhere.

    Args:
        sku: Product SKU needed.
        warehouse_id: Warehouse that needs the stock.
        quantity: Units required.
    """
    sku = normalise_id(sku)
    warehouse_id = normalise_id(warehouse_id)
    destination = access.get_warehouse(warehouse_id)
    if destination is None:
        return error(f"No warehouse found with id {warehouse_id!r}.")
    if quantity <= 0:
        return error("quantity must be a positive number of units.")

    product = access.get_product(sku) or {}
    options = []
    for row in access.inventory_for_sku(sku):
        if row["warehouse_id"] == warehouse_id:
            continue
        spare = _available(row) - row["safety_stock"]
        if spare <= 0:
            continue
        donor = access.get_warehouse(row["warehouse_id"]) or {}
        transit = TRANSFER_LEAD_TIME_DAYS.get(
            (donor.get("region", ""), destination["region"]), DEFAULT_TRANSFER_DAYS
        )
        transferable = min(spare, quantity)
        options.append(
            {
                "from_warehouse_id": row["warehouse_id"],
                "from_warehouse_name": donor.get("name"),
                "from_region": donor.get("region"),
                "transferable_units": transferable,
                "donor_available_after": _available(row) - transferable,
                "donor_safety_stock": row["safety_stock"],
                "transit_days": transit,
                "estimated_transfer_cost_usd": round(
                    transferable * TRANSFER_COST_PER_UNIT, 2
                ),
            }
        )

    # Fastest first, then largest quantity.
    options.sort(key=lambda o: (o["transit_days"], -o["transferable_units"]))
    covered = 0
    plan = []
    for option in options:
        if covered >= quantity:
            break
        take = min(option["transferable_units"], quantity - covered)
        covered += take
        plan.append({**option, "recommended_units": take})

    return as_json(
        {
            "sku": sku,
            "product_name": product.get("name"),
            "to_warehouse_id": warehouse_id,
            "to_warehouse_name": destination["name"],
            "requested_units": quantity,
            "coverable_units": covered,
            "fully_covered": covered >= quantity,
            "remaining_gap": max(0, quantity - covered),
            "recommended_transfers": plan,
            "all_donor_options": options,
            "note": (
                "Remaining gap must be covered by procurement - hand off to the "
                "Supplier Agent."
                if covered < quantity
                else "Transfers alone can cover this shortage."
            ),
        }
    )


INVENTORY_TOOLS = [
    check_inventory,
    check_warehouse_stock,
    check_warehouse_availability,
    identify_inventory_shortages,
    calculate_required_quantity,
    find_inventory_transfer,
]
