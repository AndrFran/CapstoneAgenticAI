"""Request Intake Agent.

Turns a free-text operations request into the structured description the
Supervisor routes on: incident type, intent, identifiers, and anything missing.

Owner: Team Member 1 (Request Intake & Incident Analysis Agent Engineer).
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import get_structured_llm
from ..prompts import INTAKE_PROMPT
from ..state import IntakeResult

# Deterministic fallback so a routing decision is still possible if the
# structured-output call fails (rate limit, transient 5xx, schema refusal).
ID_PATTERNS = {
    "shipment_ids": r"\bSHP-\d{4}-\d{4}\b",
    "supplier_ids": r"\bSUP-\d{3}\b",
    "skus": r"\bSKU-\d{4}\b",
    "warehouse_ids": r"\bWH-N\d{2}\b",
    "order_ids": r"\bORD-\d{4}-\d{5}\b",
    "incident_ids": r"\bINC-\d{4}-\d{4}\b",
    "routes": r"\bRTE-\d{3}\b",
}

TYPE_KEYWORDS = [
    ("damaged_goods", ("damage", "damaged", "broken", "crushed")),
    ("supplier_failure", ("supplier fail", "supplier issue", "vendor", "supplier")),
    ("route_disruption", ("route", "road", "closed", "detour", "weather")),
    ("inventory_shortage", ("stock", "inventory", "shortage", "out of stock", "sku")),
    ("shipment_delay", ("delay", "late", "held", "stuck", "customs")),
    ("status_query", ("status", "where is", "track", "eta")),
]


def _regex_intake(text: str) -> IntakeResult:
    upper = text.upper()
    found = {
        field: sorted(set(re.findall(pattern, upper)))
        for field, pattern in ID_PATTERNS.items()
    }
    lowered = text.lower()
    incident_type = "other"
    for candidate, keywords in TYPE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            incident_type = candidate
            break

    return IntakeResult(
        incident_type=incident_type,  # type: ignore[arg-type]
        intent=text.strip()[:280] or "unspecified request",
        shipment_ids=found["shipment_ids"],
        supplier_ids=found["supplier_ids"],
        skus=found["skus"],
        warehouse_ids=found["warehouse_ids"],
        order_ids=found["order_ids"],
        incident_ids=found["incident_ids"],
        quantities=[int(n) for n in re.findall(r"\b\d{2,6}\b", text)][:5],
        missing_information=[],
        is_supported=True,
    )


def analyse_request(
    user_request: str, history: list[Any] | None = None
) -> tuple[IntakeResult, bool]:
    """Extract structured intake data.

    Returns the result and whether the LLM produced it (False means the regex
    fallback was used - useful to surface in the UI and in LangSmith metadata).
    """
    history = history or []
    # Only the recent turns are needed to resolve "that shipment" style
    # references, and trimming keeps the routing call cheap.
    recent = history[-6:]

    messages = [
        SystemMessage(content=INTAKE_PROMPT),
        *recent,
        HumanMessage(content=f"Current request:\n{user_request}"),
    ]

    try:
        model = get_structured_llm("intake").with_structured_output(IntakeResult)
        result = model.invoke(messages)
        if isinstance(result, IntakeResult):
            return result, True
        if isinstance(result, dict):
            return IntakeResult(**result), True
    except Exception:  # noqa: BLE001 - degrade to the deterministic path
        pass

    return _regex_intake(user_request), False
