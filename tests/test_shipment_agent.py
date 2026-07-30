"""Shipment Agent behaviour tests.

The agent loop runs against a scripted fake chat model, so no API key is
needed (design invariant #1). What is under test is the structural contract of
the shipment workflow rather than model quality:

* a bad shipment id surfaces as recoverable data (hint + sample ids) and the
  agent loop keeps going instead of aborting;
* the delay -> affected-orders flow executes end to end through real tools;
* ``run_worker`` reports the findings contract the graph nodes rely on.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from supplychain.agents import base


class ScriptedToolCallingModel(FakeMessagesListChatModel):
    """Replays a fixed list of AI messages and accepts tool binding.

    ``create_agent`` calls ``bind_tools`` on the model; the base fake raises
    ``NotImplementedError``, so accept the binding and keep replaying.
    """

    def bind_tools(self, tools, **kwargs):
        return self


def install_scripted_model(monkeypatch, responses: list[AIMessage]) -> None:
    model = ScriptedToolCallingModel(responses=responses)
    monkeypatch.setattr(base, "get_llm", lambda tag="default": model)
    base.reset_worker_cache()


@pytest.fixture(autouse=True)
def _fresh_worker_cache():
    """Workers are lru_cached; never leak a fake model into other tests."""
    base.reset_worker_cache()
    yield
    base.reset_worker_cache()


def tool_call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


STATE = {
    "user_request": "SHP-2026-0002 is late. What's the impact?",
    "request": {
        "incident_type": "shipment_delay",
        "intent": "Assess the delay and its order impact.",
        "shipment_ids": ["SHP-2026-0002"],
    },
}


def test_bad_shipment_id_is_survivable_and_self_correctable(monkeypatch):
    install_scripted_model(
        monkeypatch,
        [
            tool_call("track_shipment", {"shipment_id": "SHP-9999-9999"}, "c1"),
            tool_call("track_shipment", {"shipment_id": "SHP-2026-0002"}, "c2"),
            AIMessage(content="SHP-2026-0002 is delayed 72h into WH-N02."),
        ],
    )

    result = base.run_worker("shipment", STATE, "Track the shipment")

    # The bad id did not abort the loop: a second call still ran.
    assert [c["name"] for c in result["tool_calls"]] == [
        "track_shipment",
        "track_shipment",
    ]
    first = json.loads(result["tool_results"][0]["content"])
    assert "error" in first
    assert first["hint"]  # what an id should look like
    assert first["sample_ids"]  # real ids the model can retry with
    second = json.loads(result["tool_results"][1]["content"])
    assert second["shipment_id"] == "SHP-2026-0002"
    assert result["summary"] == "SHP-2026-0002 is delayed 72h into WH-N02."


def test_delay_impact_flow_runs_real_tools_end_to_end(monkeypatch):
    install_scripted_model(
        monkeypatch,
        [
            tool_call("check_shipment_delay", {"shipment_id": "SHP-2026-0002"}, "c1"),
            tool_call("find_affected_orders", {"shipment_id": "SHP-2026-0002"}, "c2"),
            AIMessage(content="72h delay on SHP-2026-0002; all orders at risk."),
        ],
    )

    result = base.run_worker("shipment", STATE, "Quantify the delay and its impact")

    assert [c["name"] for c in result["tool_calls"]] == [
        "check_shipment_delay",
        "find_affected_orders",
    ]
    delay = json.loads(result["tool_results"][0]["content"])
    assert delay["is_delayed"] is True
    orders = json.loads(result["tool_results"][1]["content"])
    assert orders["order_count"] > 0

    # The findings contract the graph's worker nodes rely on.
    assert result["agent"] == "shipment"
    assert result["summary"]
    assert result["pending_action"] is None
    assert result["severity"] is None


def test_order_tools_are_bound_to_the_shipment_agent(monkeypatch):
    bound_names = {t.name for t in base.AGENT_TOOLS["shipment"]}
    assert {"get_order_details", "find_orders_by_store"} <= bound_names

    install_scripted_model(
        monkeypatch,
        [
            tool_call("get_order_details", {"order_id": "ORD-2026-00001"}, "c1"),
            AIMessage(content="ORD-2026-00001 is at risk on SHP-2026-0001."),
        ],
    )

    result = base.run_worker("shipment", STATE, "Check the order")

    order = json.loads(result["tool_results"][0]["content"])
    assert order["order_id"] == "ORD-2026-00001"
    assert order["shipment"]["shipment_id"] == "SHP-2026-0001"
