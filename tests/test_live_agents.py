"""Live model tests for the TM1 agents.

Skipped unless you pass ``--live``:

    pytest tests/test_live_agents.py --live

These call the real Gemini API, so they cost money and are not deterministic.
They exist because the hermetic suite proves the deterministic paths and says
nothing about whether the model path actually works - which is what you want to
check before a demo, after a model change, or after editing a prompt.

Owner: Team Member 1.
"""

from __future__ import annotations

import pytest

from supplychain.agents.base import run_worker
from supplychain.agents.intake import analyse_request
from supplychain.config import get_settings

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def requires_a_key():
    if not get_settings().llm_configured:
        pytest.skip("GOOGLE_API_KEY is not set")


# ---------------------------------------------------------------------------
# Request Intake Agent
# ---------------------------------------------------------------------------


def test_intake_uses_the_model_and_finds_the_identifiers():
    outcome = analyse_request(
        "SHP-2026-0002 is late into WH-N02 and I need to know the impact"
    )
    assert outcome.extracted_by == "llm", "structured output call did not succeed"
    assert outcome.result.shipment_ids == ["SHP-2026-0002"]
    assert outcome.result.warehouse_ids == ["WH-N02"]
    assert outcome.result.is_supported is True
    assert outcome.result.intent


def test_intake_normalises_sloppy_identifiers_end_to_end():
    outcome = analyse_request("whats up with shp 2026 2 into wh n2")
    assert outcome.result.shipment_ids == ["SHP-2026-0002"]
    assert outcome.result.warehouse_ids == ["WH-N02"]


def test_intake_rejects_an_unsupported_request():
    outcome = analyse_request("can you reset my email password?")
    assert outcome.result.is_supported is False


def test_intake_asks_for_what_is_missing():
    outcome = analyse_request("a shipment is late, can you look into it")
    assert outcome.result.missing_information or outcome.result.clarification_question


def test_intake_resolves_a_reference_from_entity_memory():
    entities = {"shipment_ids": ["SHP-2026-0002"]}
    outcome = analyse_request("who is the supplier on that one?", [], entities)
    assert outcome.result.shipment_ids == ["SHP-2026-0002"]


# ---------------------------------------------------------------------------
# Incident Analysis Agent
# ---------------------------------------------------------------------------


def test_incident_agent_classifies_severity_with_the_rule_engine():
    state = {
        "user_request": "SHP-2026-0002 is 72 hours late into WH-N02",
        "request": {
            "incident_type": "shipment_delay",
            "intent": "Assess the delay on SHP-2026-0002.",
            "shipment_ids": ["SHP-2026-0002"],
            "warehouse_ids": ["WH-N02"],
        },
    }
    result = run_worker("incident_analysis", state, "Assess this delay and classify it.")

    tools_used = {call["name"] for call in result["tool_calls"]}
    assert "classify_incident_severity" in tools_used, (
        "the agent must use the rule engine rather than judging severity itself"
    )
    # The fixtures make this a high-or-worse incident.
    assert result["severity"] in {"high", "critical"}
    assert result["summary"]


def test_incident_agent_spots_the_duplicate_incident():
    state = {
        "user_request": "Should we raise an incident for SHP-2026-0002?",
        "request": {
            "incident_type": "shipment_delay",
            "intent": "Decide whether SHP-2026-0002 needs a new incident.",
            "shipment_ids": ["SHP-2026-0002"],
        },
    }
    result = run_worker(
        "incident_analysis",
        state,
        "Is this already covered by an open incident? Check before recommending a new one.",
    )
    tools_used = {call["name"] for call in result["tool_calls"]}
    assert "find_related_incidents" in tools_used
    # INC-2026-0001 already covers this shipment in the fixtures.
    assert "INC-2026-0001" in result["summary"]


def test_incident_agent_cannot_write():
    """The analysis agent must not have access to any write proposal."""
    from supplychain.agents.base import AGENT_TOOLS

    names = {tool.name for tool in AGENT_TOOLS["incident_analysis"]}
    assert not names & {"propose_incident", "propose_reroute", "propose_escalation"}
