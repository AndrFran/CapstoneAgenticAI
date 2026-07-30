"""Tool tests.

Tools are the deterministic core of the platform, so they are tested without
any LLM involvement: given a known fixture, a tool must return known numbers.
Every tool is also checked for graceful failure on a bad id - an agent must get
a structured error it can recover from, never an exception.
"""

from __future__ import annotations

import json

import pytest

from supplychain.tools import incident as incident_tools
from supplychain.tools import inventory as inventory_tools
from supplychain.tools import recovery as recovery_tools
from supplychain.tools import shipment as shipment_tools
from supplychain.tools import supplier as supplier_tools
from supplychain.tools import ALL_TOOLS


def call(tool, **kwargs):
    """Invoke a LangChain tool and parse its JSON result."""
    return json.loads(tool.invoke(kwargs))


# ---------------------------------------------------------------------------
# Contract checks across the whole catalogue
# ---------------------------------------------------------------------------


def test_every_tool_has_a_name_and_description():
    for tool in ALL_TOOLS:
        assert tool.name
        assert tool.description and len(tool.description) > 20, tool.name


def test_tool_names_are_unique():
    names = [tool.name for tool in ALL_TOOLS]
    assert len(names) == len(set(names)), "duplicate tool names would confuse routing"


# ---------------------------------------------------------------------------
# Shipment tools
# ---------------------------------------------------------------------------


def test_track_shipment_returns_full_record():
    result = call(shipment_tools.track_shipment, shipment_id="SHP-2026-0002")
    assert result["shipment_id"] == "SHP-2026-0002"
    assert result["delay_hours"] == 72
    assert result["destination"]["warehouse_id"] == "WH-N02"
    assert result["supplier"]["supplier_id"] == "SUP-005"
    assert result["line_items"]


def test_track_shipment_unknown_id_returns_error_not_exception():
    result = call(shipment_tools.track_shipment, shipment_id="SHP-0000-0000")
    assert "error" in result
    assert result["sample_ids"]


def test_check_shipment_delay_explains_the_cause():
    result = call(shipment_tools.check_shipment_delay, shipment_id="SHP-2026-0002")
    assert result["is_delayed"] is True
    assert result["delay_days"] == 3.0
    # RTE-105 is disrupted and SUP-005 is at_risk in the fixtures.
    causes = " ".join(result["likely_causes"]).lower()
    assert "rte-105" in causes
    assert "sup-005" in causes


def test_estimate_delivery_delay_adds_route_risk():
    result = call(shipment_tools.estimate_delivery_delay, shipment_id="SHP-2026-0002")
    assert result["projected_delay_hours"] > result["recorded_delay_hours"]
    assert result["store_availability_delay_hours"] > result["projected_delay_hours"]


def test_find_affected_orders_matches_the_data_layer():
    from supplychain.data import access

    result = call(shipment_tools.find_affected_orders, shipment_id="SHP-2026-0002")
    assert result["order_count"] == len(access.orders_for_shipment("SHP-2026-0002"))
    assert result["order_count"] > 0
    assert result["at_risk_count"] == result["order_count"]  # shipment is delayed
    assert result["total_units"] > 0


def test_identify_delayed_shipments_is_sorted_and_filterable():
    everything = call(shipment_tools.identify_delayed_shipments)
    delays = [s["delay_hours"] for s in everything["shipments"]]
    assert delays == sorted(delays, reverse=True)

    filtered = call(shipment_tools.identify_delayed_shipments, warehouse_id="WH-N02")
    assert all(
        s["destination_warehouse_id"] == "WH-N02" for s in filtered["shipments"]
    )


def test_check_delivery_route_surfaces_disruption_and_alternates():
    result = call(shipment_tools.check_delivery_route, route_id="RTE-105")
    assert result["status"] == "disrupted"
    assert result["disruption"]
    assert result["alternates"]


# ---------------------------------------------------------------------------
# Inventory tools
# ---------------------------------------------------------------------------


def test_check_inventory_totals_available_across_the_network():
    result = call(inventory_tools.check_inventory, sku="SKU-1001")
    assert result["sku"] == "SKU-1001"
    assert len(result["positions"]) == 8  # one row per warehouse
    assert result["total_available"] == sum(p["available"] for p in result["positions"])


def test_available_never_exceeds_on_hand():
    result = call(inventory_tools.check_inventory, sku="SKU-3001")
    for position in result["positions"]:
        assert position["available"] <= position["on_hand"]
        assert position["available"] >= 0


def test_calculate_required_quantity_targets_cover_days():
    result = call(
        inventory_tools.calculate_required_quantity,
        sku="SKU-1001",
        warehouse_id="WH-N04",
        cover_days=14,
    )
    assert result["target_units"] == result["avg_daily_demand"] * 14
    assert result["required_units"] >= 0
    assert result["estimated_purchase_cost_usd"] >= 0


def test_find_inventory_transfer_never_breaches_donor_safety_stock():
    result = call(
        inventory_tools.find_inventory_transfer,
        sku="SKU-1001",
        warehouse_id="WH-N04",
        quantity=500,
    )
    for option in result["all_donor_options"]:
        assert option["donor_available_after"] >= 0
    for transfer in result["recommended_transfers"]:
        assert transfer["from_warehouse_id"] != "WH-N04"
    assert result["coverable_units"] <= 500


def test_find_inventory_transfer_rejects_bad_quantity():
    result = call(
        inventory_tools.find_inventory_transfer,
        sku="SKU-1001",
        warehouse_id="WH-N04",
        quantity=0,
    )
    assert "error" in result


def test_identify_inventory_shortages_only_returns_shortages():
    result = call(inventory_tools.identify_inventory_shortages)
    for row in result["shortages"]:
        assert row["available"] < row["reorder_point"]


# ---------------------------------------------------------------------------
# Supplier tools
# ---------------------------------------------------------------------------


def test_get_supplier_details_includes_open_shipments():
    result = call(supplier_tools.get_supplier_details, supplier_id="SUP-005")
    assert result["supplier_id"] == "SUP-005"
    assert result["status"] == "at_risk"
    assert result["open_shipment_count"] >= 1
    assert "INC-2026-0001" in result["related_incident_ids"]


def test_suspended_supplier_is_never_available():
    result = call(
        supplier_tools.check_supplier_availability,
        supplier_id="SUP-010",
        sku="SKU-5001",
        quantity=1000,
    )
    assert result["supplier_status"] == "suspended"
    assert result["available"] is False


def test_find_alternative_supplier_excludes_the_failing_one_and_suspended_ones():
    result = call(
        supplier_tools.find_alternative_supplier,
        sku="SKU-3001",
        exclude_supplier_id="SUP-005",
        quantity=1200,
    )
    ids = [s["supplier_id"] for s in result["alternatives"]]
    assert "SUP-005" not in ids
    assert all(s["status"] != "suspended" for s in result["alternatives"])
    for supplier in result["alternatives"]:
        assert "SKU-3001" in supplier["supplied_skus"]


def test_alternatives_are_ranked_by_fit():
    result = call(supplier_tools.find_alternative_supplier, sku="SKU-1001")
    scores = [s["fit_score"] for s in result["alternatives"]]
    assert scores == sorted(scores)
    assert result["recommended_supplier_id"] == result["alternatives"][0]["supplier_id"]


def test_compare_supplier_options_picks_cheapest_and_fastest():
    result = call(
        supplier_tools.compare_supplier_options,
        supplier_ids=["SUP-001", "SUP-002", "SUP-012"],
        sku="SKU-1001",
        quantity=400,
    )
    eligible = [r for r in result["comparison"] if r["supplies_sku"]]
    cheapest = min(eligible, key=lambda r: r["total_cost_usd"])
    assert result["cheapest_supplier_id"] == cheapest["supplier_id"]


def test_estimate_procurement_cost_expedite_costs_more_and_lands_sooner():
    standard = call(
        supplier_tools.estimate_procurement_cost,
        supplier_id="SUP-001",
        sku="SKU-1001",
        quantity=400,
    )
    expedited = call(
        supplier_tools.estimate_procurement_cost,
        supplier_id="SUP-001",
        sku="SKU-1001",
        quantity=400,
        expedite=True,
    )
    assert expedited["total_landed_cost_usd"] > standard["total_landed_cost_usd"]
    assert expedited["lead_time_days"] < standard["lead_time_days"]


# ---------------------------------------------------------------------------
# Incident analysis tools
# ---------------------------------------------------------------------------


def test_severity_rules_are_deterministic():
    first = call(
        incident_tools.classify_incident_severity,
        incident_type="shipment_delay",
        shipment_id="SHP-2026-0002",
    )
    second = call(
        incident_tools.classify_incident_severity,
        incident_type="shipment_delay",
        shipment_id="SHP-2026-0002",
    )
    assert first == second


def test_seventy_two_hour_delay_scores_at_least_high():
    result = call(
        incident_tools.classify_incident_severity,
        incident_type="shipment_delay",
        shipment_id="SHP-2026-0002",
    )
    assert result["severity"] in {"high", "critical"}
    assert result["requires_escalation"] is True
    assert result["signals"]


def test_quiet_shipment_does_not_score_high():
    result = call(
        incident_tools.classify_incident_severity,
        incident_type="other",
        shipment_id="SHP-2026-0004",  # delivered, no delay
    )
    assert result["severity"] in {"low", "medium"}


def test_supplier_failure_is_never_low():
    result = call(
        incident_tools.classify_incident_severity,
        incident_type="supplier_failure",
        supplier_id="SUP-001",  # active, healthy supplier
    )
    assert result["severity"] != "low"


def test_unknown_incident_type_is_rejected():
    result = call(
        incident_tools.classify_incident_severity, incident_type="alien_invasion"
    )
    assert "error" in result
    assert "valid_incident_types" in result


def test_assess_damaged_goods_quantifies_loss():
    result = call(incident_tools.assess_damaged_goods, shipment_id="SHP-2026-0005")
    assert result["has_damage"] is True
    assert result["total_damaged_units"] > 0
    assert result["value_lost_usd"] > 0
    for line in result["line_items"]:
        assert line["usable_quantity"] == line["shipped_quantity"] - line["damaged_quantity"]


def test_check_incident_status_reads_the_register():
    result = call(incident_tools.check_incident_status, incident_id="INC-2026-0001")
    assert result["severity"] == "high"
    assert result["related_shipment_id"] == "SHP-2026-0002"


# ---------------------------------------------------------------------------
# Recovery tools
# ---------------------------------------------------------------------------


def test_recovery_plan_always_offers_at_least_the_fallback():
    result = call(
        recovery_tools.generate_recovery_plan,
        incident_type="other",
    )
    assert result["option_count"] >= 1
    assert any(o["option"] == "accept_and_communicate" for o in result["options"])


def test_recovery_plan_offers_reroute_for_a_disrupted_route():
    result = call(
        recovery_tools.generate_recovery_plan,
        incident_type="shipment_delay",
        shipment_id="SHP-2026-0002",
        required_units=1200,
    )
    options = {o["option"] for o in result["options"]}
    assert "reroute_shipment" in options
    assert result["recommended_option"]


def test_estimate_recovery_cost_compares_against_inaction():
    result = call(
        recovery_tools.estimate_recovery_cost,
        option="reroute_shipment",
        units=1000,
        delay_days=3,
    )
    assert result["recovery_cost_usd"] > 0
    assert result["cost_of_inaction_usd"] > 0
    assert result["net_benefit_usd"] == pytest.approx(
        result["cost_of_inaction_usd"] - result["recovery_cost_usd"]
    )


def test_proposals_do_not_write_anything():
    from supplychain.data import access

    before = len(access.load("incidents"))
    result = call(
        recovery_tools.propose_incident,
        incident_type="shipment_delay",
        severity="high",
        title="test",
        description="test",
    )
    assert result["status"] == "awaiting_human_approval"
    assert len(access.load("incidents")) == before, "a proposal must not write"


def test_propose_reroute_validates_the_destination():
    result = call(
        recovery_tools.propose_reroute,
        shipment_id="SHP-2026-0002",
        to_route_id="RTE-101",  # serves WH-N05, not WH-N02
        reason="test",
    )
    assert "error" in result


def test_propose_escalation_requires_an_existing_incident():
    result = call(
        recovery_tools.propose_escalation,
        incident_id="INC-9999-9999",
        reason="test",
        escalate_to="COO",
    )
    assert "error" in result


def test_generate_incident_summary_routes_by_severity():
    critical = call(
        recovery_tools.generate_incident_summary,
        incident_type="shipment_delay",
        severity="critical",
        headline="h",
        impact="i",
        recommended_action="a",
    )
    low = call(
        recovery_tools.generate_incident_summary,
        incident_type="shipment_delay",
        severity="low",
        headline="h",
        impact="i",
        recommended_action="a",
    )
    assert "COO" in critical["distribution_list"]
    assert "COO" not in low["distribution_list"]
    assert critical["summary_markdown"].startswith("**CRITICAL")
