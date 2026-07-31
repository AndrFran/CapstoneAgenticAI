"""Tests for the front-end presentation layer.

These cover the two things that can go wrong quietly in a themed UI: the header
disagreeing with the agents about the same dataset, and an identifier from the
data layer being interpolated into HTML unescaped.
"""

from __future__ import annotations

import json

from supplychain.tools.shipment import identify_delayed_shipments
from supplychain.ui import theme
from supplychain.ui.overview import fleet_snapshot


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_counts_the_fixture_network():
    snapshot = fleet_snapshot()
    assert snapshot.ok
    assert snapshot.shipments == 28
    assert snapshot.open_incidents >= 1
    assert snapshot.value_at_risk > 0


def test_snapshot_agrees_with_the_delayed_shipments_tool():
    """The header and the agent answer the same question the same way.

    If these ever diverge, the demo shows one number in the KPI strip and a
    different one in the reply underneath it.
    """
    tool_count = json.loads(identify_delayed_shipments.invoke({}))["delayed_count"]
    assert fleet_snapshot().delayed == tool_count


def test_snapshot_flags_the_network_as_disrupted():
    # The fixtures are a disruption scenario, so the hero lane shows a blocked leg.
    assert fleet_snapshot().disrupted is True


def test_snapshot_survives_a_broken_data_layer(monkeypatch):
    from supplychain.ui import overview

    monkeypatch.setattr(
        overview.access, "load", lambda *_: (_ for _ in ()).throw(OSError("gone"))
    )
    snapshot = overview.fleet_snapshot()
    assert snapshot.ok is False
    assert snapshot.shipments == 0


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def test_chips_escape_their_values():
    html = theme.chips([("shipment_ids", "<script>alert(1)</script>")])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_chips_label_each_identifier_by_kind():
    html = theme.chips([("shipment_ids", "SHP-2026-0002"), ("skus", "SKU-1001")])
    assert "SHP-2026-0002" in html and "SKU-1001" in html
    assert "shipment" in html and "SKU" in html


def test_route_chain_brackets_the_workers_with_supervisor_and_response():
    html = theme.route_chain(["shipment", "inventory"])
    assert html.index("Supervisor") < html.index("Shipment") < html.index("Response")
    assert html.count("nr-chain__hop") == 3  # four steps, three connectors


def test_route_chain_handles_an_empty_route():
    html = theme.route_chain([])
    assert "Supervisor" in html and "Response" in html


def test_severity_pill_uses_the_severity_colour():
    assert theme.SEVERITY_COLOURS["critical"] in theme.severity_pill("critical")
    # An unknown severity must not blow up or borrow a warm colour.
    assert theme.ACCENT in theme.severity_pill("unknown")


def test_lane_only_blocks_legs_when_the_network_is_disrupted():
    assert "nr-lane__leg--blocked" not in theme.lane(disrupted=False)
    assert theme.lane(disrupted=True).count("nr-lane__leg--blocked") == 2


def test_every_agent_in_the_graph_has_an_icon():
    from supplychain.graph import WORKER_NODES

    for name in (*WORKER_NODES, "intake", "supervisor", "approval", "respond"):
        assert name in theme.AGENT_META, f"{name} would render as a bullet"


def test_entity_labels_cover_the_conversation_entity_fields():
    from supplychain.memory import ENTITY_FIELDS

    assert set(ENTITY_FIELDS) <= set(theme.ENTITY_META)


# ---------------------------------------------------------------------------
# Production polish: icons, no raw serialisation
# ---------------------------------------------------------------------------


def test_no_emoji_anywhere_in_the_presentation_layer():
    """Emoji are the clearest "prototype" tell, and they cannot take the theme
    colour. Every glyph goes through the Material Symbols font instead."""
    import re
    from pathlib import Path

    emoji = re.compile(
        "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff]"
    )
    for name in ("theme.py", "overview.py", "visuals.py"):
        source = (Path(theme.__file__).parent / name).read_text(encoding="utf-8")
        found = emoji.findall(source)
        assert not found, f"{name} still contains emoji: {found}"


def test_icons_are_ligature_names_not_glyphs():
    for mapping in (theme.AGENT_META, theme.ENTITY_META):
        for glyph, _ in mapping.values():
            assert glyph.replace("_", "").isalnum(), glyph
            assert glyph.islower()


def test_kv_grid_renders_booleans_as_words():
    html = theme.kv_grid({"is_supported": True, "escalated": False})
    assert ">Yes<" in html and ">No<" in html
    assert "True" not in html and "False" not in html


def test_kv_grid_keeps_a_false_but_drops_an_empty():
    html = theme.kv_grid({"escalated": False, "notes": "", "tags": []})
    assert "escalated" in html
    assert "notes" not in html and "tags" not in html


def test_kv_grid_is_empty_when_everything_is_empty():
    assert theme.kv_grid({"a": None, "b": ""}) == ""


def test_kv_grid_escapes_values():
    html = theme.kv_grid({"title": "<img src=x onerror=1>"})
    assert "<img" not in html and "&lt;img" in html


def test_kv_grid_flattens_lists():
    assert "SHP-1, SHP-2" in theme.kv_grid({"shipment_ids": ["SHP-1", "SHP-2"]})


def test_conversation_dot_rules_only_cover_rated_conversations(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(theme.st, "html", lambda body: written.append(body))

    theme.conversation_dots([("thread-a", "critical"), ("thread-b", None)])
    assert len(written) == 1
    assert "st-key-open-thread-a" in written[0]
    assert theme.SEVERITY_COLOURS["critical"] in written[0]
    assert "thread-b" not in written[0]


def test_conversation_dots_writes_nothing_without_severities(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(theme.st, "html", lambda body: written.append(body))

    theme.conversation_dots([("thread-a", None)])
    assert written == []


def test_conversation_dots_sanitise_the_thread_id(monkeypatch):
    """A thread id reaches a CSS selector, so it must not be able to close it."""
    written: list[str] = []
    monkeypatch.setattr(theme.st, "html", lambda body: written.append(body))

    theme.conversation_dots([("a}b{color:red", "high")])
    assert "{color:red" not in written[0]
    assert written[0].count("{") == 1
