"""Entry point for LangGraph Server, and therefore for LangGraph Studio.

Studio is the graph view that sits alongside the traces in LangSmith: nodes and
edges drawn from the real topology, each run steppable, state inspectable at
every hop, and the approval ``interrupt()`` presented as something you resume
rather than something you read about afterwards.

It renders whatever a **LangGraph Server** exposes, which is the one thing the
Streamlit deployment is not. Streamlit Cloud runs the graph in-process behind a
chat UI; it speaks no LangGraph protocol. So the app on Streamlit Cloud sends
*traces* to LangSmith (that part already works), while Studio needs a server
pointed at this module - ``langgraph dev`` locally, or a LangGraph Platform
deployment. Both serve the same graph object, so what Studio draws is the
system, not a diagram of it that can drift.

Two differences from the in-process build in :mod:`runner`:

* **No checkpointer.** The server owns persistence - threads, history and
  interrupt resumption are its job. Compiling our own ``MemorySaver`` in would
  be ignored, and reasoning about which one was in force would be worse than
  useless.
* **Tracing is configured on import**, because nothing else here runs
  ``runner`` first.

Configuration comes from the same ``.env`` as everywhere else; ``langgraph.json``
points at it.
"""

from __future__ import annotations

from .graph import build_workflow
from .observability import configure_tracing

__all__ = ["make_graph"]


def make_graph():
    """Compile the workflow for a LangGraph Server.

    ``langgraph.json`` names this factory rather than a module-level graph
    object so that tracing configuration and compilation happen when the server
    asks for the graph, not as an import side effect of anything that happens
    to touch this package.
    """
    configure_tracing()
    return build_workflow().compile()
