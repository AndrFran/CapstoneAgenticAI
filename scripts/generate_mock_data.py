"""Generate the deterministic mock dataset for the NovaRetail platform.

The capstone brief says the client's systems "expose mock REST APIs or JSON
data". This script produces the JSON fixtures that back the tool layer, so the
dataset is reproducible and reviewable in git rather than hand-maintained.

Run from the repo root:

    python scripts/generate_mock_data.py

Output: src/supplychain/data/mock/*.json  (committed)
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20260730
OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "supplychain" / "data" / "mock"

# "Today" for the scenario. Fixed so ETAs and delays stay stable across runs.
TODAY = date(2026, 7, 30)

REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West", "Northwest"]

WAREHOUSES = [
    ("WH-N01", "Newark RDC", "Northeast", "Newark, NJ"),
    ("WH-N02", "Atlanta RDC", "Southeast", "Atlanta, GA"),
    ("WH-N03", "Columbus RDC", "Midwest", "Columbus, OH"),
    ("WH-N04", "Dallas RDC", "Southwest", "Dallas, TX"),
    ("WH-N05", "Ontario RDC", "West", "Ontario, CA"),
    ("WH-N06", "Kent RDC", "Northwest", "Kent, WA"),
    ("WH-N07", "Memphis RDC", "Southeast", "Memphis, TN"),
    ("WH-N08", "Denver RDC", "Midwest", "Denver, CO"),
]

PRODUCTS = [
    ("SKU-1001", "AuroraTop 14 Laptop", "Electronics", 780.00),
    ("SKU-1002", "PulseBuds Wireless Earbuds", "Electronics", 42.50),
    ("SKU-1003", "NovaCharge 65W Adapter", "Electronics", 18.90),
    ("SKU-2001", "HearthPan 12in Skillet", "Home", 27.40),
    ("SKU-2002", "LumenLamp LED Desk Lamp", "Home", 21.75),
    ("SKU-2003", "CottonCloud Bath Towel Set", "Home", 33.10),
    ("SKU-3001", "TrailRunner Sneakers", "Apparel", 46.20),
    ("SKU-3002", "AllWeather Rain Jacket", "Apparel", 58.00),
    ("SKU-4001", "VitaBlend Protein Powder", "Grocery", 24.60),
    ("SKU-4002", "MorningHarvest Coffee 1kg", "Grocery", 16.35),
    ("SKU-5001", "PlayLab Building Blocks 500pc", "Toys", 29.99),
    ("SKU-5002", "SkyGlide RC Drone Mini", "Toys", 63.40),
]

SUPPLIERS = [
    # id, name, region, country, tier
    ("SUP-001", "Meridian Electronics Ltd", "West", "Vietnam", "strategic"),
    ("SUP-002", "Kestrel Components Co", "West", "Taiwan", "strategic"),
    ("SUP-003", "Basalt Home Goods", "Midwest", "Mexico", "preferred"),
    ("SUP-004", "Lakeshore Housewares", "Midwest", "USA", "preferred"),
    ("SUP-005", "Vantage Apparel Group", "Southeast", "Bangladesh", "strategic"),
    ("SUP-006", "Cedarline Textiles", "Southeast", "Portugal", "backup"),
    ("SUP-007", "Harvest Foods Coop", "Northeast", "USA", "preferred"),
    ("SUP-008", "Terrafirma Grocers", "Northeast", "Canada", "backup"),
    ("SUP-009", "BrightPlay Manufacturing", "Southwest", "China", "preferred"),
    ("SUP-010", "Orbit Toyworks", "Southwest", "Poland", "backup"),
    ("SUP-011", "Northwind Logistics Supply", "Northwest", "USA", "backup"),
    ("SUP-012", "Solstice Trading Partners", "West", "India", "preferred"),
]

# Which SKU families each supplier can fulfil.
SUPPLIER_CATEGORIES = {
    "SUP-001": ["Electronics"],
    "SUP-002": ["Electronics"],
    "SUP-003": ["Home"],
    "SUP-004": ["Home"],
    "SUP-005": ["Apparel"],
    "SUP-006": ["Apparel"],
    "SUP-007": ["Grocery"],
    "SUP-008": ["Grocery"],
    "SUP-009": ["Toys"],
    "SUP-010": ["Toys"],
    "SUP-011": ["Home", "Toys"],
    "SUP-012": ["Electronics", "Apparel"],
}

CARRIERS = ["Atlas Freight", "BlueLine Express", "Continental Cargo", "Pacific Rail"]

ROUTE_DEFS = [
    # id, origin, destination_wh, mode, transit_days, status, disruption
    ("RTE-101", "Port of Los Angeles, CA", "WH-N05", "truck", 2, "clear", None),
    ("RTE-102", "Port of Los Angeles, CA", "WH-N04", "rail", 4, "congested",
     "Rail yard backlog at Barstow, CA adding 12-24h dwell time."),
    ("RTE-103", "Port of Seattle, WA", "WH-N06", "truck", 1, "clear", None),
    ("RTE-104", "Port of Newark, NJ", "WH-N01", "truck", 1, "clear", None),
    ("RTE-105", "Port of Savannah, GA", "WH-N02", "truck", 2, "disrupted",
     "I-16 closed westbound after bridge inspection; detour adds 6h."),
    ("RTE-106", "Laredo, TX crossing", "WH-N04", "truck", 3, "clear", None),
    ("RTE-107", "Chicago, IL hub", "WH-N03", "rail", 2, "clear", None),
    ("RTE-108", "Memphis, TN air hub", "WH-N07", "air", 1, "clear", None),
    ("RTE-109", "Denver, CO hub", "WH-N08", "truck", 2, "weather_hold",
     "High-wind advisory on I-70 through Eisenhower Tunnel."),
    ("RTE-110", "Port of Los Angeles, CA", "WH-N03", "rail", 5, "clear", None),
    ("RTE-111", "Port of Charleston, SC", "WH-N02", "truck", 3, "clear", None),
    ("RTE-112", "Kansas City, MO hub", "WH-N08", "rail", 3, "clear", None),
]

# Alternate route options keyed by the primary route. An alternate must serve
# the same destination warehouse - the reroute tool enforces that, so the data
# has to respect it too.
ROUTE_ALTERNATES = {
    "RTE-102": ["RTE-106"],
    "RTE-106": ["RTE-102"],
    "RTE-105": ["RTE-111"],
    "RTE-111": ["RTE-105"],
    "RTE-107": ["RTE-110"],
    "RTE-110": ["RTE-107"],
    "RTE-109": ["RTE-112"],
    "RTE-112": ["RTE-109"],
}

SHIPMENT_STATUSES = [
    "in_transit",
    "delayed",
    "at_customs",
    "delivered",
    "damaged",
    "out_for_delivery",
]

STORES = [f"STR-{n:03d}" for n in range(101, 131)]


def iso(d: date) -> str:
    return d.isoformat()


def products_for(supplier_id: str) -> list[str]:
    cats = SUPPLIER_CATEGORIES[supplier_id]
    return [sku for sku, _, cat, _ in PRODUCTS if cat in cats]


def build_warehouses() -> list[dict]:
    return [
        {
            "warehouse_id": wid,
            "name": name,
            "region": region,
            "location": loc,
            "capacity_pallets": 12000 + (idx * 750),
            "manager": f"ops.{wid.lower()}@novaretail.example",
        }
        for idx, (wid, name, region, loc) in enumerate(WAREHOUSES)
    ]


def build_products() -> list[dict]:
    return [
        {
            "sku": sku,
            "name": name,
            "category": cat,
            "unit_cost": cost,
            "retail_price": round(cost * 1.42, 2),
            "unit_of_measure": "each",
        }
        for sku, name, cat, cost in PRODUCTS
    ]


def build_suppliers(rng: random.Random) -> list[dict]:
    rows = []
    for sid, name, region, country, tier in SUPPLIERS:
        skus = products_for(sid)
        status = "active"
        if sid == "SUP-005":
            status = "at_risk"  # scripted supplier failure scenario
        elif sid == "SUP-010":
            status = "suspended"
        rows.append(
            {
                "supplier_id": sid,
                "name": name,
                "region": region,
                "country": country,
                "tier": tier,
                "status": status,
                "contact_email": f"orders@{name.split()[0].lower()}.example",
                "contact_phone": f"+1-555-{rng.randint(1000, 9999)}",
                "lead_time_days": rng.randint(4, 21),
                "reliability_score": round(rng.uniform(0.68, 0.985), 3),
                "on_time_delivery_rate": round(rng.uniform(0.71, 0.99), 3),
                "quality_score": round(rng.uniform(0.80, 0.99), 3),
                "min_order_qty": rng.choice([50, 100, 250, 500]),
                "supplied_skus": skus,
                "unit_prices": {
                    sku: round(
                        next(c for s, _, _, c in PRODUCTS if s == sku)
                        * rng.uniform(0.86, 1.09),
                        2,
                    )
                    for sku in skus
                },
                "notes": (
                    "Capacity constrained through Q3; two missed windows in July."
                    if status == "at_risk"
                    else (
                        "Suspended pending quality audit closure."
                        if status == "suspended"
                        else ""
                    )
                ),
            }
        )
    return rows


def build_routes() -> list[dict]:
    return [
        {
            "route_id": rid,
            "origin": origin,
            "destination_warehouse_id": dest,
            "mode": mode,
            "transit_days": days,
            "status": status,
            "disruption": disruption,
            "alternate_route_ids": ROUTE_ALTERNATES.get(rid, []),
            "last_updated": f"{iso(TODAY)}T06:00:00Z",
        }
        for rid, origin, dest, mode, days, status, disruption in ROUTE_DEFS
    ]


def build_shipments(rng: random.Random) -> list[dict]:
    routes = {r[0]: r for r in ROUTE_DEFS}
    shipments: list[dict] = []

    # Scripted shipments first so the demo scenarios in the README always work.
    scripted = [
        # shipment_id, supplier, route, status, delay_hours, skus
        ("SHP-2026-0001", "SUP-001", "RTE-102", "delayed", 36,
         [("SKU-1001", 400), ("SKU-1003", 900)]),
        ("SHP-2026-0002", "SUP-005", "RTE-105", "delayed", 72,
         [("SKU-3001", 1200), ("SKU-3002", 600)]),
        ("SHP-2026-0003", "SUP-009", "RTE-106", "in_transit", 0,
         [("SKU-5001", 800)]),
        ("SHP-2026-0004", "SUP-007", "RTE-104", "delivered", 0,
         [("SKU-4001", 1500), ("SKU-4002", 2000)]),
        ("SHP-2026-0005", "SUP-003", "RTE-109", "damaged", 12,
         [("SKU-2001", 700), ("SKU-2002", 500)]),
        ("SHP-2026-0006", "SUP-002", "RTE-101", "at_customs", 18,
         [("SKU-1002", 2500)]),
    ]

    def make(sid, supplier, route_id, status, delay, items):
        route = routes[route_id]
        transit = route[4]
        departed = TODAY - timedelta(days=transit + rng.randint(0, 2))
        original_eta = departed + timedelta(days=transit)
        eta = original_eta + timedelta(hours=delay)
        return {
            "shipment_id": sid,
            "supplier_id": supplier,
            "carrier": rng.choice(CARRIERS),
            "route_id": route_id,
            "origin": route[1],
            "destination_warehouse_id": route[2],
            "mode": route[3],
            "status": status,
            "departed_at": f"{iso(departed)}T08:00:00Z",
            "original_eta": f"{iso(original_eta)}T17:00:00Z",
            "eta": f"{iso(eta.date() if isinstance(eta, datetime) else eta)}T17:00:00Z",
            "delay_hours": delay,
            "last_scan_location": route[1] if status != "delivered" else route[2],
            "last_scan_at": f"{iso(TODAY)}T04:30:00Z",
            "line_items": [
                {
                    "sku": sku,
                    "quantity": qty,
                    "damaged_quantity": (
                        int(qty * 0.18) if status == "damaged" else 0
                    ),
                }
                for sku, qty in items
            ],
            "value_usd": round(
                sum(
                    qty * next(c for s, _, _, c in PRODUCTS if s == sku)
                    for sku, qty in items
                ),
                2,
            ),
            "priority": rng.choice(["standard", "standard", "expedited"]),
        }

    for row in scripted:
        shipments.append(make(*row))

    # Filler shipments so the dataset feels like real operational volume.
    next_id = 7
    for supplier_id, *_ in SUPPLIERS:
        for _ in range(rng.randint(1, 2)):
            candidate_routes = [r[0] for r in ROUTE_DEFS]
            route_id = rng.choice(candidate_routes)
            status = rng.choice(SHIPMENT_STATUSES)
            delay = rng.choice([0, 0, 0, 6, 12, 24, 48]) if status != "delivered" else 0
            skus = products_for(supplier_id)
            items = [
                (sku, rng.choice([150, 300, 450, 600, 900, 1200]))
                for sku in rng.sample(skus, k=min(len(skus), rng.randint(1, 2)))
            ]
            shipments.append(
                make(
                    f"SHP-2026-{next_id:04d}",
                    supplier_id,
                    route_id,
                    status,
                    delay,
                    items,
                )
            )
            next_id += 1

    return shipments


def build_orders(rng: random.Random, shipments: list[dict]) -> list[dict]:
    orders: list[dict] = []
    n = 1
    for shp in shipments:
        # Each shipment backs 1-3 customer/replenishment orders.
        for _ in range(rng.randint(1, 3)):
            item = rng.choice(shp["line_items"])
            promised = date.fromisoformat(shp["original_eta"][:10]) + timedelta(
                days=rng.randint(1, 4)
            )
            at_risk = shp["status"] in {"delayed", "damaged", "at_customs"}
            orders.append(
                {
                    "order_id": f"ORD-2026-{n:05d}",
                    "store_id": rng.choice(STORES),
                    "customer_ref": f"CUST-{rng.randint(10000, 99999)}",
                    "sku": item["sku"],
                    "quantity": max(10, int(item["quantity"] * rng.uniform(0.05, 0.3))),
                    "promised_date": iso(promised),
                    "status": "at_risk" if at_risk else "on_track",
                    "shipment_id": shp["shipment_id"],
                    "channel": rng.choice(["store_replenishment", "ecommerce", "b2b"]),
                }
            )
            n += 1
    return orders


def build_inventory(rng: random.Random) -> list[dict]:
    rows = []
    for wid, *_ in WAREHOUSES:
        for sku, _, cat, _ in PRODUCTS:
            daily_demand = rng.randint(20, 260)
            reorder_point = daily_demand * rng.randint(4, 8)
            on_hand = rng.randint(0, reorder_point * 3)
            # Scripted shortages that pair with the delayed shipments above.
            if (wid, sku) in {("WH-N04", "SKU-1001"), ("WH-N02", "SKU-3001")}:
                on_hand = rng.randint(0, 40)
            rows.append(
                {
                    "warehouse_id": wid,
                    "sku": sku,
                    "on_hand": on_hand,
                    "reserved": min(on_hand, rng.randint(0, 120)),
                    "in_transit": rng.choice([0, 0, 200, 400, 900]),
                    "reorder_point": reorder_point,
                    "safety_stock": daily_demand * 3,
                    "avg_daily_demand": daily_demand,
                    "last_counted": iso(TODAY - timedelta(days=rng.randint(1, 14))),
                }
            )
    return rows


def main() -> None:
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    warehouses = build_warehouses()
    products = build_products()
    suppliers = build_suppliers(rng)
    routes = build_routes()
    shipments = build_shipments(rng)
    orders = build_orders(rng, shipments)
    inventory = build_inventory(rng)

    datasets = {
        "warehouses.json": warehouses,
        "products.json": products,
        "suppliers.json": suppliers,
        "routes.json": routes,
        "shipments.json": shipments,
        "orders.json": orders,
        "inventory.json": inventory,
        # Seed incidents; runtime-created incidents live in data/runtime/.
        "incidents.json": [
            {
                "incident_id": "INC-2026-0001",
                "type": "shipment_delay",
                "severity": "high",
                "status": "open",
                "title": "SHP-2026-0002 delayed 72h into WH-N02",
                "description": (
                    "Apparel inbound from SUP-005 held by I-16 closure on RTE-105. "
                    "Two store replenishment waves at risk."
                ),
                "related_shipment_id": "SHP-2026-0002",
                "related_supplier_id": "SUP-005",
                "related_warehouse_id": "WH-N02",
                "related_skus": ["SKU-3001", "SKU-3002"],
                "created_at": f"{iso(TODAY - timedelta(days=1))}T14:20:00Z",
                "owner": "central.supplychain@novaretail.example",
                "escalated": False,
            }
        ],
        "_meta.json": {
            "generated_by": "scripts/generate_mock_data.py",
            "seed": SEED,
            "scenario_date": iso(TODAY),
            "counts": {
                "warehouses": len(warehouses),
                "products": len(products),
                "suppliers": len(suppliers),
                "routes": len(routes),
                "shipments": len(shipments),
                "orders": len(orders),
                "inventory_rows": len(inventory),
            },
        },
    }

    for filename, payload in datasets.items():
        path = OUT_DIR / filename
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        size = len(payload) if isinstance(payload, list) else 1
        print(f"wrote {path.relative_to(OUT_DIR.parents[4])}  ({size} records)")


if __name__ == "__main__":
    main()
