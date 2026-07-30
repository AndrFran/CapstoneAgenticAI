"""Agent layer.

Six agents, three kinds:

* **Intake** (``intake``) - structured extraction, no tools.
* **Supervisor** (``supervisor``) - routing only, no tools.
* **Workers** (``base``) - ReAct loops over a curated tool list:
  incident_analysis, shipment, inventory, supplier, recovery.
* **Responder** (``responder``) - writes the final answer.
"""

from .base import AGENT_TOOLS, get_worker, run_worker
from .intake import analyse_request
from .responder import write_response
from .supervisor import decide_route

__all__ = [
    "AGENT_TOOLS",
    "analyse_request",
    "decide_route",
    "get_worker",
    "run_worker",
    "write_response",
]
