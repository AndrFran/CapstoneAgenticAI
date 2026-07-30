"""Turn a finished turn into the panels that visualise its answer.

The answers the agents write are prose. This module works out what that prose
is *about* and rebuilds the underlying numbers, so the UI can draw a delay
timeline or a stock-cover chart under the text.

Two decisions worth understanding before changing anything here:

**The numbers are recomputed from the tool layer, not parsed out of the
answer.** Asking the model for chart data, or regexing figures out of its prose,
would put hallucinated numbers on a chart that looks authoritative. The tools
are deterministic Python over the same committed fixtures the agents read, so
recomputing costs microseconds and cannot disagree with reality. The trade-off
is that a chart shows the data *now*: if an approved reroute has since changed
the shipment, the panel reflects the change and the prose above it will not.

**Which panels appear is driven by the tools the agents actually called**, which
`findings[agent]["tool_calls"]` already records, and not by keyword-matching the
question. A panel therefore mirrors the work that was done - if no agent looked
at inventory, no stock chart appears, however many SKUs the question mentioned.

Pure data: no Streamlit, no pandas, no model. `streamlit_app.render_visuals`
draws whatever this returns.

Owner: Team Member 1 (chat UI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..data import access
from ..tools.common import SCENARIO_TODAY, parse_iso

# Private, and imported deliberately: `_position` is the exact shape
# `check_inventory` returns, so reusing it means the chart cannot state a
# different days-of-cover than the agent did. Same reasoning as DELAY_STATUSES
# in overview.py. If TM3 changes the cover maths, both move together.
from ..tools.inventory import _position
from ..tools.shipment import DELAY_STATUSES

# At most three panels under one answer. Past that it stops being a visual
# summary and becomes a dashboard the reader has to work through.
MAX_PANELS = 3
MAX_TIMELINES = 2

# The tool names that make each panel relevant. Keyed on what the agents ran.
SHIPMENT_TOOLS = {
    "track_shipment",
    "get_shipment_status",
    "check_shipment_delay",
    "estimate_delivery_delay",
    "find_affected_orders",
    "get_shipment_details",
    "assess_damaged_goods",
    "check_delivery_route",
    "check_route_status",
}
BACKLOG_TOOLS = {"identify_delayed_shipments"}
SKU_STOCK_TOOLS = {
    "check_inventory",
    "check_warehouse_stock",
    "identify_inventory_shortages",
    "find_inventory_transfer",
    "calculate_required_quantity",
}
WAREHOUSE_STOCK_TOOLS = {"check_warehouse_availability", "identify_inventory_shortages"}
SUPPLIER_TOOLS = {
    "find_alternative_supplier",
    "compare_supplier_options",
    "check_supplier_availability",
    "estimate_procurement_cost",
    "get_supplier_details",
}

# Noon on the scenario date. Built through parse_iso so it carries the same
# timezone as the fixtures - mixing an aware ETA with a naive "now" raises.
# Wall-clock dates would put every shipment years in the past (invariant 7).
NOW = parse_iso(f"{SCENARIO_TODAY.isoformat()}T12:00:00Z")


# ---------------------------------------------------------------------------
# Panel types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShipmentTimeline:
    """One shipment drawn as a lane: planned transit, then the slip."""

    shipment_id: str
    status: str
    carrier: str
    mode: str
    origin: str
    destination: str
    departed_at: str | None
    original_eta: str | None
    revised_eta: str | None
    delay_hours: int
    last_scan_location: str | None
    last_scan_at: str | None
    route_id: str | None
    route_status: str | None
    disruption: str | None
    value_usd: float
    units: int
    orders_at_risk: int
    # Percentages across the lane, 0-100.
    planned_pct: float
    slip_pct: float
    now_pct: float
    scan_pct: float | None

    title = "Shipment lane"

    @property
    def is_late(self) -> bool:
        return self.delay_hours > 0


@dataclass(frozen=True)
class StockPositions:
    """One SKU across every warehouse that carries it."""

    sku: str
    product_name: str | None
    highlight_warehouse: str | None
    rows: list[dict[str, Any]] = field(default_factory=list)

    title = "Stock cover by warehouse"


@dataclass(frozen=True)
class WarehouseStock:
    """One warehouse, worst cover first."""

    warehouse_id: str
    warehouse_name: str | None
    short_count: int
    rows: list[dict[str, Any]] = field(default_factory=list)

    title = "Lines below reorder point"


@dataclass(frozen=True)
class DelayBacklog:
    """Where the delayed shipments are piling up."""

    total: int
    rows: list[dict[str, Any]] = field(default_factory=list)

    title = "Delay backlog by destination"


@dataclass(frozen=True)
class SupplierOptions:
    """Who else can supply this SKU, and on what terms."""

    sku: str
    exclude_supplier_id: str | None
    rows: list[dict[str, Any]] = field(default_factory=list)

    title = "Supplier options"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _pct(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round(max(0.0, min(100.0, part / whole * 100)), 2)


def shipment_timeline(shipment_id: str) -> ShipmentTimeline | None:
    shipment = access.get_shipment(shipment_id)
    if not shipment:
        return None

    route = access.get_route(shipment.get("route_id") or "") or {}
    warehouse = access.get_warehouse(shipment.get("destination_warehouse_id") or "") or {}

    departed = parse_iso(shipment.get("departed_at"))
    original = parse_iso(shipment.get("original_eta"))
    revised = parse_iso(shipment.get("eta"))
    scan = parse_iso(shipment.get("last_scan_at"))

    # The lane runs from departure to whichever comes last: the promise, the
    # revised arrival, or now (a shipment can be late with no revised ETA yet).
    ends = [d for d in (original, revised, NOW) if d]
    if departed and ends:
        span = max(ends)
        total = (span - departed).total_seconds()
        planned = _pct(((original or span) - departed).total_seconds(), total)
        slip = (
            _pct((revised - (original or revised)).total_seconds(), total)
            if revised and original and revised > original
            else 0.0
        )
        now_pct = _pct((NOW - departed).total_seconds(), total) if NOW else 0.0
        scan_pct = _pct((scan - departed).total_seconds(), total) if scan else None
    else:
        planned = slip = now_pct = 0.0
        scan_pct = None

    line_items = shipment.get("line_items") or []
    orders = [
        o
        for o in access.load("orders")
        if o.get("shipment_id") == shipment["shipment_id"]
    ]

    return ShipmentTimeline(
        shipment_id=shipment["shipment_id"],
        status=shipment.get("status", "unknown"),
        carrier=shipment.get("carrier", ""),
        mode=shipment.get("mode", ""),
        origin=shipment.get("origin", ""),
        destination=warehouse.get("name") or shipment.get("destination_warehouse_id", ""),
        departed_at=shipment.get("departed_at"),
        original_eta=shipment.get("original_eta"),
        revised_eta=shipment.get("eta"),
        delay_hours=shipment.get("delay_hours", 0) or 0,
        last_scan_location=shipment.get("last_scan_location"),
        last_scan_at=shipment.get("last_scan_at"),
        route_id=shipment.get("route_id"),
        route_status=route.get("status"),
        disruption=route.get("disruption") or None,
        value_usd=shipment.get("value_usd") or 0.0,
        units=sum(item.get("quantity", 0) for item in line_items),
        orders_at_risk=len(orders),
        planned_pct=planned,
        slip_pct=slip,
        now_pct=now_pct,
        scan_pct=scan_pct,
    )


def stock_positions(sku: str, highlight: str | None = None) -> StockPositions | None:
    rows = [row for row in access.load("inventory") if row.get("sku") == sku]
    if not rows:
        return None

    product = access.get_product(sku) or {}
    positions = [_position(row) for row in rows]
    # Most stock first: the question behind this panel is almost always
    # "who can cover the site that is short?".
    positions.sort(key=lambda p: p["available"], reverse=True)

    return StockPositions(
        sku=sku,
        product_name=product.get("name"),
        highlight_warehouse=highlight,
        rows=positions,
    )


def warehouse_stock(warehouse_id: str, limit: int = 8) -> WarehouseStock | None:
    rows = [row for row in access.load("inventory") if row.get("warehouse_id") == warehouse_id]
    if not rows:
        return None

    warehouse = access.get_warehouse(warehouse_id) or {}
    positions = [_position(row) for row in rows]
    short = [p for p in positions if p["below_reorder_point"]]
    # Worst cover first - the lines an operator has to act on today.
    positions.sort(key=lambda p: p["days_of_cover"])

    return WarehouseStock(
        warehouse_id=warehouse_id,
        warehouse_name=warehouse.get("name"),
        short_count=len(short),
        rows=positions[:limit],
    )


def delay_backlog() -> DelayBacklog | None:
    delayed = [
        s
        for s in access.load("shipments")
        if s.get("status") in DELAY_STATUSES or (s.get("delay_hours") or 0) > 0
    ]
    if not delayed:
        return None

    by_warehouse: dict[str, dict[str, Any]] = {}
    for shipment in delayed:
        key = shipment.get("destination_warehouse_id") or "unknown"
        bucket = by_warehouse.setdefault(
            key,
            {
                "warehouse_id": key,
                "warehouse_name": (access.get_warehouse(key) or {}).get("name") or key,
                "shipments": 0,
                "delay_hours": 0,
                "value_usd": 0.0,
            },
        )
        bucket["shipments"] += 1
        bucket["delay_hours"] += shipment.get("delay_hours", 0) or 0
        bucket["value_usd"] += shipment.get("value_usd") or 0.0

    rows = sorted(
        by_warehouse.values(),
        key=lambda r: (r["shipments"], r["delay_hours"]),
        reverse=True,
    )
    return DelayBacklog(total=len(delayed), rows=rows)


def supplier_options(sku: str, exclude: str | None = None) -> SupplierOptions | None:
    rows = []
    for supplier in access.load("suppliers"):
        if sku not in (supplier.get("supplied_skus") or []):
            continue
        rows.append(
            {
                "supplier_id": supplier["supplier_id"],
                "name": supplier["name"],
                "tier": supplier["tier"],
                "status": supplier["status"],
                "country": supplier["country"],
                "unit_price": (supplier.get("unit_prices") or {}).get(sku),
                "lead_time_days": supplier["lead_time_days"],
                "reliability_score": supplier["reliability_score"],
                "on_time_delivery_rate": supplier["on_time_delivery_rate"],
                "is_excluded": supplier["supplier_id"] == exclude,
            }
        )
    if not rows:
        return None

    rows.sort(key=lambda r: (r["unit_price"] is None, r["unit_price"] or 0))
    return SupplierOptions(sku=sku, exclude_supplier_id=exclude, rows=rows)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def tools_used(meta: dict[str, Any]) -> set[str]:
    """Every tool name called by any agent during the turn."""
    names: set[str] = set()
    for finding in (meta.get("findings") or {}).values():
        if isinstance(finding, dict):
            names.update(finding.get("tool_calls") or [])
    return names


def build_panels(meta: dict[str, Any]) -> list[Any]:
    """The panels to draw under one answer, most relevant first."""
    # No guard on an empty request: the backlog panel answers "which shipments
    # are delayed right now", which names no identifier at all.
    request = meta.get("request") or {}
    used = tools_used(meta)
    shipments = request.get("shipment_ids") or []
    skus = request.get("skus") or []
    warehouses = request.get("warehouse_ids") or []
    suppliers = request.get("supplier_ids") or []

    panels: list[Any] = []

    if shipments and used & SHIPMENT_TOOLS:
        for shipment_id in shipments[:MAX_TIMELINES]:
            panel = shipment_timeline(shipment_id)
            if panel:
                panels.append(panel)

    if used & BACKLOG_TOOLS:
        panel = delay_backlog()
        if panel:
            panels.append(panel)

    if skus and used & SKU_STOCK_TOOLS:
        panel = stock_positions(skus[0], highlight=warehouses[0] if warehouses else None)
        if panel:
            panels.append(panel)
    elif warehouses and used & WAREHOUSE_STOCK_TOOLS:
        panel = warehouse_stock(warehouses[0])
        if panel:
            panels.append(panel)

    if skus and used & SUPPLIER_TOOLS:
        panel = supplier_options(skus[0], exclude=suppliers[0] if suppliers else None)
        if panel:
            panels.append(panel)

    return panels[:MAX_PANELS]
