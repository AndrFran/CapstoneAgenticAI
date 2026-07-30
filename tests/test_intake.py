"""Request Intake Agent tests.

The LLM stage is unavailable in tests (no API key), so `analyse_request` runs
its deterministic path - which is exactly the path that has to keep working
when a model call fails in production.

Owner: Team Member 1.
"""

from __future__ import annotations

import pytest

from supplychain.agents.intake import (
    IntakeOutcome,
    analyse_request,
    classify_by_keyword,
    extract_identifiers,
    extract_quantities,
    validate_identifiers,
)
from supplychain.memory import most_recent, update_entities


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SHP-2026-0002", "SHP-2026-0002"),
        ("shp-2026-0002", "SHP-2026-0002"),
        ("shp 2026 2", "SHP-2026-0002"),          # unpadded, space separated
        ("SHP_2026_02", "SHP-2026-0002"),
        ("shipment SHP/2026/0002 is late", "SHP-2026-0002"),
    ],
)
def test_shipment_ids_normalise_to_canonical_form(text, expected):
    assert extract_identifiers(text)["shipment_ids"] == [expected]


@pytest.mark.parametrize(
    "text,field,expected",
    [
        ("sup 5", "supplier_ids", "SUP-005"),
        ("SUP-005", "supplier_ids", "SUP-005"),
        ("sku 1001", "skus", "SKU-1001"),
        ("sku-1", "skus", "SKU-0001"),
        ("wh n4", "warehouse_ids", "WH-N04"),
        ("WH-N04", "warehouse_ids", "WH-N04"),
        ("wh 4", "warehouse_ids", "WH-N04"),
        ("ord 2026 7", "order_ids", "ORD-2026-00007"),
        ("inc 2026 1", "incident_ids", "INC-2026-0001"),
        ("rte 105", "route_ids", "RTE-105"),
    ],
)
def test_every_identifier_kind_normalises(text, field, expected):
    assert extract_identifiers(text)[field] == [expected]


def test_multiple_identifiers_in_one_request():
    found = extract_identifiers(
        "shp 2026 2 from sup 5 into wh n2 is short on sku 3001, see inc 2026 1"
    )
    assert found["shipment_ids"] == ["SHP-2026-0002"]
    assert found["supplier_ids"] == ["SUP-005"]
    assert found["warehouse_ids"] == ["WH-N02"]
    assert found["skus"] == ["SKU-3001"]
    assert found["incident_ids"] == ["INC-2026-0001"]


def test_duplicate_mentions_are_collapsed():
    found = extract_identifiers("SHP-2026-0002 and shp 2026 2 are the same load")
    assert found["shipment_ids"] == ["SHP-2026-0002"]


def test_text_without_identifiers_yields_nothing():
    assert extract_identifiers("something is late somewhere") == {}


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------


def test_quantities_are_extracted_without_swallowing_ids():
    assert extract_quantities("we need 1,200 units of SKU-3001") == [1200]


def test_years_are_not_mistaken_for_quantities():
    assert 2026 not in extract_quantities("SHP-2026-0002 needs 400 units by 2026")


def test_id_digits_are_not_mistaken_for_quantities():
    # 2026 and 0002 belong to the shipment id, 900 is the real quantity.
    assert extract_quantities("SHP-2026-0002 carries 900 units") == [900]


# ---------------------------------------------------------------------------
# Keyword classification (fallback path)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("this pallet arrived damaged", "damaged_goods"),
        ("we are out of stock on SKU-1001", "inventory_shortage"),
        ("where is my shipment", "status_query"),
        ("the supplier missed two windows", "supplier_failure"),
        ("the route is closed after a bridge inspection", "route_disruption"),
        ("this load is stuck in customs", "shipment_delay"),
        ("hello there", "other"),
    ],
)
def test_keyword_classification(text, expected):
    assert classify_by_keyword(text) == expected


# ---------------------------------------------------------------------------
# Validation against the data layer
# ---------------------------------------------------------------------------


def test_known_identifiers_validate():
    valid, unknown = validate_identifiers({"shipment_ids": ["SHP-2026-0002"]})
    assert valid == {"shipment_ids": ["SHP-2026-0002"]}
    assert unknown == []


def test_unknown_identifier_is_reported_with_suggestions():
    valid, unknown = validate_identifiers({"shipment_ids": ["SHP-2026-0003X"]})
    assert valid == {}
    assert len(unknown) == 1
    assert unknown[0]["field"] == "shipment_ids"
    assert unknown[0]["suggestions"], "expected a close-match suggestion"


def test_validation_separates_good_from_bad_in_one_request():
    valid, unknown = validate_identifiers(
        {"shipment_ids": ["SHP-2026-0002", "SHP-9999-9999"]}
    )
    assert valid["shipment_ids"] == ["SHP-2026-0002"]
    assert [u["value"] for u in unknown] == ["SHP-9999-9999"]


# ---------------------------------------------------------------------------
# Entity memory
# ---------------------------------------------------------------------------


def test_entity_memory_keeps_most_recent_first():
    entities = update_entities({}, {"shipment_ids": ["SHP-2026-0001"]})
    entities = update_entities(entities, {"shipment_ids": ["SHP-2026-0002"]})
    assert entities["shipment_ids"] == ["SHP-2026-0002", "SHP-2026-0001"]
    assert most_recent(entities, "shipment_ids") == "SHP-2026-0002"


def test_entity_memory_is_capped():
    entities: dict[str, list[str]] = {}
    for number in range(1, 6):
        entities = update_entities(
            entities, {"shipment_ids": [f"SHP-2026-000{number}"]}, depth=3
        )
    assert len(entities["shipment_ids"]) == 3


def test_entity_memory_deduplicates():
    entities = update_entities(
        {"shipment_ids": ["SHP-2026-0002"]}, {"shipment_ids": ["SHP-2026-0002"]}
    )
    assert entities["shipment_ids"] == ["SHP-2026-0002"]


# ---------------------------------------------------------------------------
# End-to-end intake (deterministic path)
# ---------------------------------------------------------------------------


def test_analyse_request_returns_a_routable_outcome_without_an_llm():
    outcome = analyse_request("What's the status of shp 2026 2?")
    assert isinstance(outcome, IntakeOutcome)
    assert outcome.extracted_by == "regex"
    assert outcome.result.shipment_ids == ["SHP-2026-0002"]
    assert outcome.result.is_supported is True
    assert outcome.entities["shipment_ids"] == ["SHP-2026-0002"]


def test_analyse_request_carries_forward_a_referring_expression():
    entities = {"shipment_ids": ["SHP-2026-0002"]}
    outcome = analyse_request("who is the supplier on that one?", [], entities)
    assert outcome.result.shipment_ids == ["SHP-2026-0002"]
    assert "shipment_ids" in outcome.resolved_from_memory


def test_analyse_request_does_not_carry_forward_without_a_reference():
    entities = {"shipment_ids": ["SHP-2026-0002"]}
    outcome = analyse_request("which shipments are delayed right now?", [], entities)
    assert outcome.result.shipment_ids == []
    assert outcome.resolved_from_memory == []


def test_explicit_identifier_beats_memory():
    entities = {"shipment_ids": ["SHP-2026-0002"]}
    outcome = analyse_request("what about that shipment SHP-2026-0001?", [], entities)
    assert outcome.result.shipment_ids == ["SHP-2026-0001"]
    assert "shipment_ids" not in outcome.resolved_from_memory


def test_unknown_identifier_becomes_missing_information():
    outcome = analyse_request("check shipment SHP-9999-9999 please")
    assert outcome.unknown_identifiers
    assert any(
        "does not exist" in note for note in outcome.result.missing_information
    )
    # The bad id is not passed downstream for an agent to choke on.
    assert outcome.result.shipment_ids == []


def test_as_request_carries_intake_metadata():
    payload = analyse_request("status of SHP-2026-0002").as_request()
    assert payload["extracted_by"] == "regex"
    assert payload["prompt_version"]
    assert payload["resolved_from_memory"] == []
    assert payload["shipment_ids"] == ["SHP-2026-0002"]


def test_analyse_request_never_raises_on_junk():
    for text in ("", "   ", "!!!", "SHP-", "wh-n", "🚚"):
        outcome = analyse_request(text)
        assert isinstance(outcome, IntakeOutcome)
        assert outcome.result.intent
