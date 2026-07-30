"""Data access layer for NovaRetail's back-end systems.

Every tool goes through this module instead of touching files or HTTP directly.
Two interchangeable back ends:

* **JSON fixtures** (default) - reads ``data/mock/*.json``.
* **Mock REST API** - set ``SUPPLYCHAIN_API_BASE_URL`` and each collection is
  fetched from ``{base}/{collection}`` instead. This is the switch to flip when
  the instructor-supplied mock APIs are wired up; no tool code changes.

Writes (new incidents, status changes) go to ``data/runtime/*.json`` so the
committed fixtures stay pristine between demo runs.

Owner: Team Member 2 & 3 (shipment/order and inventory/supplier data), with the
runtime incident store used by Team Member 4.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ..config import MOCK_DATA_DIR, RUNTIME_DATA_DIR, get_settings

Record = dict[str, Any]

_CACHE: dict[str, list[Record]] = {}
_LOCK = threading.Lock()

COLLECTIONS = (
    "warehouses",
    "products",
    "suppliers",
    "routes",
    "shipments",
    "orders",
    "inventory",
    "incidents",
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_from_file(collection: str) -> list[Record]:
    path = MOCK_DATA_DIR / f"{collection}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing fixture {path}. Run: python scripts/generate_mock_data.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _load_from_api(collection: str) -> list[Record]:
    base = get_settings().api_base_url
    response = requests.get(f"{base}/{collection}", timeout=15)
    response.raise_for_status()
    payload = response.json()
    # Accept both a bare list and the common {"data": [...]} envelope.
    if isinstance(payload, dict):
        payload = payload.get("data", payload.get(collection, []))
    return list(payload)


def _runtime_path(collection: str) -> Path:
    return RUNTIME_DATA_DIR / f"{collection}.json"


def _load_runtime(collection: str) -> list[Record]:
    path = _runtime_path(collection)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_runtime(collection: str, records: list[Record]) -> None:
    RUNTIME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _runtime_path(collection).write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )


def load(collection: str) -> list[Record]:
    """Return every record in a collection (base data + runtime additions)."""
    if collection not in COLLECTIONS:
        raise ValueError(f"Unknown collection {collection!r}")

    with _LOCK:
        if collection not in _CACHE:
            settings = get_settings()
            base = (
                _load_from_api(collection)
                if settings.uses_rest_api
                else _load_from_file(collection)
            )
            _CACHE[collection] = base
        base_records = _CACHE[collection]

    runtime_records = _load_runtime(collection)
    if not runtime_records:
        return list(base_records)

    # Runtime records override base records with the same id.
    id_field = _id_field(collection)
    merged = {rec[id_field]: rec for rec in base_records}
    for rec in runtime_records:
        merged[rec[id_field]] = rec
    return list(merged.values())


def clear_cache() -> None:
    """Drop the in-memory cache (tests, and after switching data source)."""
    with _LOCK:
        _CACHE.clear()


def _id_field(collection: str) -> str:
    return {
        "warehouses": "warehouse_id",
        "products": "sku",
        "suppliers": "supplier_id",
        "routes": "route_id",
        "shipments": "shipment_id",
        "orders": "order_id",
        "incidents": "incident_id",
        "inventory": "warehouse_id",  # composite; not used for lookups
    }[collection]


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def find_one(collection: str, **filters: Any) -> Record | None:
    """First record whose fields match all filters (case-insensitive on str)."""
    for record in find_all(collection, **filters):
        return record
    return None


def find_all(collection: str, **filters: Any) -> list[Record]:
    """All records matching every filter. ``None`` filter values are ignored."""
    results = []
    for record in load(collection):
        if all(_matches(record.get(key), value) for key, value in filters.items()):
            results.append(record)
    return results


def _matches(actual: Any, expected: Any) -> bool:
    if expected is None:
        return True
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().lower() == expected.strip().lower()
    return actual == expected


def get_shipment(shipment_id: str) -> Record | None:
    return find_one("shipments", shipment_id=shipment_id)


def get_supplier(supplier_id: str) -> Record | None:
    return find_one("suppliers", supplier_id=supplier_id)


def get_product(sku: str) -> Record | None:
    return find_one("products", sku=sku)


def get_warehouse(warehouse_id: str) -> Record | None:
    return find_one("warehouses", warehouse_id=warehouse_id)


def get_route(route_id: str) -> Record | None:
    return find_one("routes", route_id=route_id)


def get_incident(incident_id: str) -> Record | None:
    return find_one("incidents", incident_id=incident_id)


def get_inventory_row(warehouse_id: str, sku: str) -> Record | None:
    for row in load("inventory"):
        if (
            row["warehouse_id"].lower() == warehouse_id.strip().lower()
            and row["sku"].lower() == sku.strip().lower()
        ):
            return row
    return None


def inventory_for_sku(sku: str) -> list[Record]:
    return [row for row in load("inventory") if row["sku"].lower() == sku.strip().lower()]


def orders_for_shipment(shipment_id: str) -> list[Record]:
    return find_all("orders", shipment_id=shipment_id)


def suppliers_for_sku(sku: str) -> list[Record]:
    sku_norm = sku.strip().upper()
    return [s for s in load("suppliers") if sku_norm in s.get("supplied_skus", [])]


def search_suppliers(query: str) -> list[Record]:
    """Fuzzy-ish supplier search over id, name, region and country."""
    q = query.strip().lower()
    if not q:
        return []
    hits = []
    for supplier in load("suppliers"):
        haystack = " ".join(
            str(supplier.get(field, ""))
            for field in ("supplier_id", "name", "region", "country", "tier")
        ).lower()
        if q in haystack:
            hits.append(supplier)
    return hits


def get_order(order_id: str) -> Record | None:
    return find_one("orders", order_id=order_id)


def orders_for_store(store_id: str) -> list[Record]:
    return find_all("orders", store_id=store_id)


# ---------------------------------------------------------------------------
# Writes (runtime store)
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_incident_id() -> str:
    existing = [rec["incident_id"] for rec in load("incidents")]
    numbers = [
        int(inc.rsplit("-", 1)[-1]) for inc in existing if inc.rsplit("-", 1)[-1].isdigit()
    ]
    return f"INC-2026-{(max(numbers) + 1) if numbers else 1:04d}"


def append_incident(incident: Record) -> Record:
    """Persist a new incident to the runtime store and return it."""
    records = _load_runtime("incidents")
    records.append(incident)
    _save_runtime("incidents", records)
    return incident


def upsert_incident(incident: Record) -> Record:
    """Insert or update an incident in the runtime store."""
    records = _load_runtime("incidents")
    for index, existing in enumerate(records):
        if existing["incident_id"] == incident["incident_id"]:
            records[index] = incident
            break
    else:
        records.append(incident)
    _save_runtime("incidents", records)
    return incident


def update_shipment(shipment_id: str, changes: Record) -> Record | None:
    """Apply changes to a shipment and persist them to the runtime store."""
    shipment = get_shipment(shipment_id)
    if shipment is None:
        return None
    updated = {**shipment, **changes, "last_updated": now_iso()}
    records = _load_runtime("shipments")
    for index, existing in enumerate(records):
        if existing["shipment_id"] == shipment_id:
            records[index] = updated
            break
    else:
        records.append(updated)
    _save_runtime("shipments", records)
    return updated


def reset_runtime_store() -> None:
    """Delete every runtime override. Handy between demo runs and in tests."""
    if RUNTIME_DATA_DIR.exists():
        for path in RUNTIME_DATA_DIR.glob("*.json"):
            path.unlink()
    clear_cache()
