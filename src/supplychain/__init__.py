"""NovaRetail intelligent supply chain disruption management platform.

A LangGraph multi-agent system that takes a natural-language operations
request, routes it to the right supply chain function, calls the required
tools, and returns a coordinated answer.

Public entry points:

    from supplychain import build_graph, run_turn
"""

from .config import get_settings
from .graph import build_graph
from .runner import run_turn

__all__ = ["build_graph", "run_turn", "get_settings"]
__version__ = "0.1.0"
