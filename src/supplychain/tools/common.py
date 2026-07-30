"""Shared helpers for the tool layer.

Tools return JSON strings so the model always sees a stable, parseable shape,
and so failures come back as data (``{"error": ...}``) rather than exceptions
that would abort the agent loop.

Owner: shared.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

# The scenario "today" baked into the fixtures. Kept here so date maths in the
# tools matches the dataset instead of the wall clock.
SCENARIO_TODAY = date(2026, 7, 30)

SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def as_json(payload: Any) -> str:
    """Serialise a tool result for the model."""
    return json.dumps(payload, indent=2, default=str)


def error(message: str, **context: Any) -> str:
    """A structured, recoverable tool error.

    The agent sees what went wrong and what it could try instead, which is what
    keeps a bad id from derailing the whole conversation.
    """
    return as_json({"error": message, **context})


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_until(value: str | None, *, reference: date = SCENARIO_TODAY) -> int | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return (parsed.date() - reference).days


def normalise_id(value: str | None) -> str:
    return (value or "").strip().upper()


def max_severity(*severities: str | None) -> str:
    """Highest severity among the inputs (defaults to ``low``)."""
    ranks = [
        SEVERITY_ORDER.index(s.lower())
        for s in severities
        if s and s.lower() in SEVERITY_ORDER
    ]
    return SEVERITY_ORDER[max(ranks)] if ranks else "low"
