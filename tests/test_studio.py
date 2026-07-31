"""LangGraph Server / Studio entry point.

``langgraph.json`` is loaded by a server we do not run in CI, so nothing else
would notice it going stale: a renamed node, a moved module or a graph that
quietly acquires a checkpointer all fail at ``langgraph dev`` time, on someone
else's machine, minutes before a demo. These tests resolve the config the same
way the server does and compare the result against the real graph.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from supplychain import studio
from supplychain.graph import WORKER_NODES, build_workflow

CONFIG_PATH = Path(__file__).resolve().parents[1] / "langgraph.json"


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_config_declares_one_graph(config):
    assert list(config["graphs"]) == ["supplychain"]
    assert config["dependencies"] == ["."]
    assert config["env"] == ".env"


def test_the_declared_entry_point_resolves(config):
    """Resolve `module:attribute` exactly as the server does."""
    module_path, _, attribute = config["graphs"]["supplychain"].partition(":")
    # A file path would be loaded as a standalone module, which breaks every
    # relative import in the package - hence a dotted module path.
    assert not module_path.endswith(".py"), "use a dotted module path, not a file path"
    factory = getattr(importlib.import_module(module_path), attribute)
    assert factory is studio.make_graph


def test_the_served_graph_has_no_checkpointer_of_its_own():
    """The server owns persistence; ours would be ignored or fight it."""
    assert studio.make_graph().checkpointer is None


def test_the_served_graph_is_the_graph_the_app_runs():
    served = studio.make_graph().get_graph()
    in_process = build_workflow().compile().get_graph()

    assert {n.id for n in served.nodes.values()} == {
        n.id for n in in_process.nodes.values()
    }
    assert {(e.source, e.target) for e in served.edges} == {
        (e.source, e.target) for e in in_process.edges
    }


def test_every_node_studio_draws_is_reachable():
    """A node with no inbound edge is dead code that still shows up in Studio."""
    graph = studio.make_graph().get_graph()
    targets = {e.target for e in graph.edges}
    for name in (*WORKER_NODES, "intake", "supervisor", "approval", "respond"):
        assert name in targets, f"{name} is drawn but nothing routes to it"
