"""Tests for the answer visualisations.

The panels are drawn from the deterministic tool layer rather than from the
model's prose, so they can be checked for exact numbers like any other tool.
Two properties matter most: a panel only appears when an agent actually did the
matching work, and the geometry of the shipment lane is to scale.
"""

from __future__ import annotations

import json

import pytest

from supplychain.data import access
from supplychain.tools.inventory import check_inventory
from supplychain.tools.shipment import identify_delayed_shipments
from supplychain.ui import visuals


def meta(request: dict | None = None, **agent_tools: list[str]) -> dict:
    """A turn's trace metadata: what intake extracted, what each agent called."""
    return {
        "request": request or {},
        "findings": {
            agent: {"summary": "", "tool_calls": tools}
            for agent, tools in agent_tools.items()
        },
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_no_panels_when_no_agent_did_the_work():
    """Mentioning a SKU is not enough - some agent has to have looked at it."""
    assert visuals.build_panels(meta({"skus": ["SKU-1001"]})) == []


def test_a_shipment_lookup_draws_the_lane():
    panels = visuals.build_panels(
        meta({"shipment_ids": ["SHP-2026-0002"]}, shipment=["track_shipment"])
    )
    assert [type(p).__name__ for p in panels] == ["ShipmentTimeline"]


def test_an_inventory_question_draws_stock_cover():
    panels = visuals.build_panels(
        meta(
            {"skus": ["SKU-1001"], "warehouse_ids": ["WH-N04"]},
            inventory=["check_inventory", "find_inventory_transfer"],
        )
    )
    assert [type(p).__name__ for p in panels] == ["StockPositions"]
    assert panels[0].highlight_warehouse == "WH-N04"


def test_a_warehouse_question_draws_that_warehouse():
    panels = visuals.build_panels(
        meta(
            {"warehouse_ids": ["WH-N02"]},
            inventory=["check_warehouse_availability"],
        )
    )
    assert [type(p).__name__ for p in panels] == ["WarehouseStock"]


def test_the_backlog_panel_needs_no_identifiers():
    """"Which shipments are delayed right now?" names nothing at all."""
    panels = visuals.build_panels(meta({}, shipment=["identify_delayed_shipments"]))
    assert [type(p).__name__ for p in panels] == ["DelayBacklog"]


def test_a_sourcing_question_draws_supplier_options():
    panels = visuals.build_panels(
        meta(
            {"skus": ["SKU-3001"], "supplier_ids": ["SUP-005"]},
            supplier=["find_alternative_supplier"],
        )
    )
    assert [type(p).__name__ for p in panels] == ["SupplierOptions"]
    assert panels[0].exclude_supplier_id == "SUP-005"


def test_panels_are_capped():
    panels = visuals.build_panels(
        meta(
            {
                "shipment_ids": ["SHP-2026-0001", "SHP-2026-0002", "SHP-2026-0003"],
                "skus": ["SKU-1001"],
            },
            shipment=["track_shipment", "identify_delayed_shipments"],
            inventory=["check_inventory"],
            supplier=["find_alternative_supplier"],
        )
    )
    assert len(panels) == visuals.MAX_PANELS


def test_an_unknown_identifier_is_skipped_not_raised():
    assert visuals.build_panels(
        meta({"shipment_ids": ["SHP-9999-9999"]}, shipment=["track_shipment"])
    ) == []


def test_tools_used_survives_malformed_findings():
    assert visuals.tools_used({"findings": {"shipment": "a plain string"}}) == set()


# ---------------------------------------------------------------------------
# Shipment lane geometry
# ---------------------------------------------------------------------------


def test_the_lane_is_drawn_to_scale():
    """SHP-2026-0002: departed 28th 08:00, promised 30th 17:00, now 2nd 17:00.

    That is 57h of planned transit and 72h of slip across a 129h lane.
    """
    lane = visuals.shipment_timeline("SHP-2026-0002")
    assert lane.delay_hours == 72
    assert lane.planned_pct == pytest.approx(57 / 129 * 100, abs=0.05)
    assert lane.slip_pct == pytest.approx(72 / 129 * 100, abs=0.05)
    # Planned plus slip fills the lane exactly when the revised ETA is last.
    assert lane.planned_pct + lane.slip_pct == pytest.approx(100, abs=0.05)


def test_the_now_marker_uses_the_scenario_date_not_the_wall_clock():
    """Wall-clock dates would put every fixture shipment years in the past."""
    lane = visuals.shipment_timeline("SHP-2026-0002")
    # Noon on 2026-07-30 is 52h after an 07-28 08:00 departure.
    assert lane.now_pct == pytest.approx(52 / 129 * 100, abs=0.05)
    assert 0 < lane.now_pct < 100


def test_an_on_time_shipment_has_no_slip():
    on_time = next(
        s for s in access.load("shipments") if not (s.get("delay_hours") or 0)
    )
    lane = visuals.shipment_timeline(on_time["shipment_id"])
    assert lane.slip_pct == 0.0
    assert lane.is_late is False


def test_the_lane_carries_the_route_disruption():
    lane = visuals.shipment_timeline("SHP-2026-0002")
    assert lane.route_id == "RTE-105"
    assert lane.route_status == "disrupted"
    assert "bridge" in (lane.disruption or "")


def test_an_unknown_shipment_returns_none():
    assert visuals.shipment_timeline("SHP-0000-0000") is None


# ---------------------------------------------------------------------------
# The panels must not contradict the tools
# ---------------------------------------------------------------------------


def test_stock_panel_matches_check_inventory():
    panel = visuals.stock_positions("SKU-1001")
    tool = json.loads(check_inventory.invoke({"sku": "SKU-1001"}))
    by_warehouse = {p["warehouse_id"]: p for p in tool["positions"]}

    assert len(panel.rows) == len(by_warehouse)
    for row in panel.rows:
        expected = by_warehouse[row["warehouse_id"]]
        assert row["available"] == expected["available"]
        assert row["days_of_cover"] == expected["days_of_cover"]


def test_backlog_panel_matches_identify_delayed_shipments():
    panel = visuals.delay_backlog()
    tool = json.loads(identify_delayed_shipments.invoke({}))
    assert panel.total == tool["delayed_count"]
    assert sum(row["shipments"] for row in panel.rows) == tool["delayed_count"]


def test_backlog_is_ordered_worst_first():
    rows = visuals.delay_backlog().rows
    assert rows == sorted(
        rows, key=lambda r: (r["shipments"], r["delay_hours"]), reverse=True
    )


def test_supplier_options_are_cheapest_first():
    panel = visuals.supplier_options("SKU-3001")
    prices = [row["unit_price"] for row in panel.rows]
    assert prices == sorted(prices)
    assert all(row["unit_price"] is not None for row in panel.rows)


def test_supplier_options_only_list_suppliers_who_carry_the_sku():
    panel = visuals.supplier_options("SKU-3001")
    for row in panel.rows:
        supplier = access.get_supplier(row["supplier_id"])
        assert "SKU-3001" in supplier["supplied_skus"]


def test_warehouse_panel_lists_worst_cover_first():
    panel = visuals.warehouse_stock("WH-N02")
    cover = [row["days_of_cover"] for row in panel.rows]
    assert cover == sorted(cover)
    assert panel.short_count >= 0
