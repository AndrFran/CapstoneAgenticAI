"""Data layer tests - fixtures load, relationships hold, writes persist."""

from __future__ import annotations

import pytest

from supplychain.data import access


def test_every_collection_loads():
    for collection in access.COLLECTIONS:
        records = access.load(collection)
        assert isinstance(records, list)
        assert records, f"{collection} is empty"


def test_shipments_reference_real_suppliers_routes_and_warehouses():
    supplier_ids = {s["supplier_id"] for s in access.load("suppliers")}
    route_ids = {r["route_id"] for r in access.load("routes")}
    warehouse_ids = {w["warehouse_id"] for w in access.load("warehouses")}
    skus = {p["sku"] for p in access.load("products")}

    for shipment in access.load("shipments"):
        assert shipment["supplier_id"] in supplier_ids
        assert shipment["route_id"] in route_ids
        assert shipment["destination_warehouse_id"] in warehouse_ids
        for item in shipment["line_items"]:
            assert item["sku"] in skus


def test_orders_reference_real_shipments():
    shipment_ids = {s["shipment_id"] for s in access.load("shipments")}
    for order in access.load("orders"):
        assert order["shipment_id"] in shipment_ids


def test_inventory_covers_every_warehouse_sku_pair():
    warehouses = access.load("warehouses")
    products = access.load("products")
    assert len(access.load("inventory")) == len(warehouses) * len(products)


def test_lookup_is_case_insensitive():
    assert access.get_shipment("shp-2026-0001") is not None
    assert access.get_supplier("sup-001") is not None


def test_unknown_ids_return_none():
    assert access.get_shipment("SHP-9999-9999") is None
    assert access.get_supplier("SUP-999") is None
    assert access.get_inventory_row("WH-N99", "SKU-1001") is None


def test_unknown_collection_raises():
    with pytest.raises(ValueError):
        access.load("not_a_collection")


def test_runtime_incident_write_is_visible_and_isolated():
    before = len(access.load("incidents"))
    incident_id = access.next_incident_id()
    access.append_incident(
        {
            "incident_id": incident_id,
            "type": "shipment_delay",
            "severity": "high",
            "status": "open",
            "title": "test incident",
            "description": "",
        }
    )
    after = access.load("incidents")
    assert len(after) == before + 1
    assert access.get_incident(incident_id) is not None

    access.reset_runtime_store()
    assert len(access.load("incidents")) == before


def test_shipment_update_persists_to_runtime_store():
    updated = access.update_shipment("SHP-2026-0001", {"status": "in_transit"})
    assert updated is not None
    assert access.get_shipment("SHP-2026-0001")["status"] == "in_transit"


def test_suppliers_for_sku_only_returns_suppliers_of_that_sku():
    for supplier in access.suppliers_for_sku("SKU-1001"):
        assert "SKU-1001" in supplier["supplied_skus"]
