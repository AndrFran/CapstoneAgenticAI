"""Tool layer - one module per supply chain function.

Each agent gets a curated tool list rather than the whole catalogue, which is
what keeps routing decisions crisp and traces readable.
"""

from .incident import INCIDENT_TOOLS
from .inventory import INVENTORY_TOOLS
from .recovery import PROPOSAL_TOOL_NAMES, RECOVERY_TOOLS
from .shipment import SHIPMENT_TOOLS
from .supplier import SUPPLIER_TOOLS

ALL_TOOLS = [
    *INCIDENT_TOOLS,
    *SHIPMENT_TOOLS,
    *INVENTORY_TOOLS,
    *SUPPLIER_TOOLS,
    *RECOVERY_TOOLS,
]

TOOLS_BY_NAME = {tool.name: tool for tool in ALL_TOOLS}

__all__ = [
    "INCIDENT_TOOLS",
    "SHIPMENT_TOOLS",
    "INVENTORY_TOOLS",
    "SUPPLIER_TOOLS",
    "RECOVERY_TOOLS",
    "PROPOSAL_TOOL_NAMES",
    "ALL_TOOLS",
    "TOOLS_BY_NAME",
]
