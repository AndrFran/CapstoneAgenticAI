"""Request Intake Agent.

Turns a free-text operations request into the structured description the
Supervisor routes on: incident type, intent, identifiers, and anything missing.

The LLM does the classification and the intent summary. Three deterministic
stages wrap it, because operations staff do not type canonical ids and because
routing must still work when a model call fails:

1. **Extraction + normalisation** - ``shp 2026 2``, ``SHP-2026-0002`` and
   ``shipment 2026-0002`` all become ``SHP-2026-0002``.
2. **Validation** - every identifier is checked against the data layer, and an
   unknown one gets close-match suggestions instead of being passed downstream
   to fail inside an agent.
3. **Entity memory** - "that shipment" resolves from what the conversation has
   already mentioned, so follow-up turns work even on the regex fallback path.

Owner: Team Member 1 (Request Intake & Incident Analysis Agent Engineer).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from ..data import access
from ..llm import get_structured_llm
from ..memory import ENTITY_FIELDS, most_recent, update_entities
from ..observability import traced
from ..prompts import INTAKE_PROMPT, prompt_version
from ..state import IntakeResult

# ---------------------------------------------------------------------------
# Identifier extraction and normalisation
# ---------------------------------------------------------------------------


def _pad(value: str, width: int) -> str:
    return value.zfill(width)


@dataclass(frozen=True)
class IdentifierSpec:
    """How one kind of identifier is written, matched and canonicalised."""

    field: str
    pattern: str
    canonical: Callable[[re.Match[str]], str]
    collection: str
    id_field: str

    def find(self, text: str) -> list[str]:
        found: list[str] = []
        for match in re.finditer(self.pattern, text, flags=re.IGNORECASE):
            value = self.canonical(match)
            if value not in found:
                found.append(value)
        return found


IDENTIFIER_SPECS: tuple[IdentifierSpec, ...] = (
    IdentifierSpec(
        field="shipment_ids",
        # SHP-2026-0002 / shp 2026 2 / SHP2026002
        pattern=r"\bSHP[\s\-_/]*(\d{4})[\s\-_/]*(\d{1,4})\b",
        canonical=lambda m: f"SHP-{m.group(1)}-{_pad(m.group(2), 4)}",
        collection="shipments",
        id_field="shipment_id",
    ),
    IdentifierSpec(
        field="supplier_ids",
        pattern=r"\bSUP[\s\-_/]*(\d{1,3})\b",
        canonical=lambda m: f"SUP-{_pad(m.group(1), 3)}",
        collection="suppliers",
        id_field="supplier_id",
    ),
    IdentifierSpec(
        field="skus",
        pattern=r"\bSKU[\s\-_/]*(\d{1,4})\b",
        canonical=lambda m: f"SKU-{_pad(m.group(1), 4)}",
        collection="products",
        id_field="sku",
    ),
    IdentifierSpec(
        field="warehouse_ids",
        # WH-N04 / wh n4 / warehouse WH04
        pattern=r"\bWH[\s\-_/]*N?[\s\-_/]*(\d{1,2})\b",
        canonical=lambda m: f"WH-N{_pad(m.group(1), 2)}",
        collection="warehouses",
        id_field="warehouse_id",
    ),
    IdentifierSpec(
        field="order_ids",
        pattern=r"\bORD[\s\-_/]*(\d{4})[\s\-_/]*(\d{1,5})\b",
        canonical=lambda m: f"ORD-{m.group(1)}-{_pad(m.group(2), 5)}",
        collection="orders",
        id_field="order_id",
    ),
    IdentifierSpec(
        field="incident_ids",
        pattern=r"\bINC[\s\-_/]*(\d{4})[\s\-_/]*(\d{1,4})\b",
        canonical=lambda m: f"INC-{m.group(1)}-{_pad(m.group(2), 4)}",
        collection="incidents",
        id_field="incident_id",
    ),
    IdentifierSpec(
        field="route_ids",
        pattern=r"\bRTE[\s\-_/]*(\d{1,3})\b",
        canonical=lambda m: f"RTE-{_pad(m.group(1), 3)}",
        collection="routes",
        id_field="route_id",
    ),
)

SPECS_BY_FIELD = {spec.field: spec for spec in IDENTIFIER_SPECS}

# Phrases that point at something already discussed rather than naming it.
REFERRING_EXPRESSIONS = re.compile(
    r"\b("
    r"that|this|those|these|it|its|it's|the same|same one|the above|"
    r"there|the shipment|the supplier|the warehouse|the order|the incident|"
    r"they|them|previous|earlier|last one"
    r")\b",
    flags=re.IGNORECASE,
)

TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("damaged_goods", ("damage", "damaged", "broken", "crushed", "spoiled", "write-off")),
    ("supplier_failure", ("supplier fail", "missed window", "vendor", "supplier issue", "supplier")),
    ("route_disruption", ("route", "road closed", "detour", "weather", "closure", "congestion")),
    ("inventory_shortage", ("stock", "inventory", "shortage", "out of stock", "short on", "replenish")),
    ("shipment_delay", ("delay", "late", "held", "stuck", "customs", "behind schedule")),
    ("status_query", ("status", "where is", "track", "eta", "when will")),
)

QUANTITY_PATTERN = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d{2,6})\s*(?:units?|pcs|pieces|cases|each)?\b"
)


@traced("normalise identifiers")
def extract_identifiers(text: str) -> dict[str, list[str]]:
    """Every identifier in the text, canonicalised. Never raises."""
    return {
        spec.field: spec.find(text or "")
        for spec in IDENTIFIER_SPECS
        if spec.find(text or "")
    }


def extract_quantities(text: str) -> list[int]:
    """Plausible unit quantities, ignoring anything that is part of an id."""
    scrubbed = text or ""
    for spec in IDENTIFIER_SPECS:
        scrubbed = re.sub(spec.pattern, " ", scrubbed, flags=re.IGNORECASE)
    values: list[int] = []
    for match in QUANTITY_PATTERN.finditer(scrubbed):
        try:
            value = int(match.group(1).replace(",", ""))
        except ValueError:
            continue
        # Four-digit years and tiny numbers are rarely order quantities.
        if 1900 <= value <= 2100 or value in values:
            continue
        values.append(value)
    return values[:5]


def classify_by_keyword(text: str) -> str:
    lowered = (text or "").lower()
    for candidate, keywords in TYPE_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return candidate
    return "other"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _known_ids(spec: IdentifierSpec) -> list[str]:
    try:
        return [record[spec.id_field] for record in access.load(spec.collection)]
    except Exception:  # noqa: BLE001 - a data problem must not break intake
        return []


@traced("validate identifiers")
def validate_identifiers(
    identifiers: dict[str, list[str]],
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Split identifiers into ones that exist and ones that do not.

    Unknown identifiers come back with close-match suggestions, which is what
    turns "SHP-2026-2000 doesn't exist" into "did you mean SHP-2026-0020?"
    before any agent wastes a tool call on it.
    """
    valid: dict[str, list[str]] = {}
    unknown: list[dict[str, Any]] = []

    for field_name, values in identifiers.items():
        spec = SPECS_BY_FIELD.get(field_name)
        if spec is None or not values:
            continue
        known = _known_ids(spec)
        known_upper = {value.upper() for value in known}
        for value in values:
            if value.upper() in known_upper:
                valid.setdefault(field_name, []).append(value)
            else:
                unknown.append(
                    {
                        "field": field_name,
                        "value": value,
                        "suggestions": difflib.get_close_matches(
                            value, known, n=3, cutoff=0.6
                        ),
                    }
                )
    return valid, unknown


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass
class IntakeOutcome:
    """Everything intake produces for one turn."""

    result: IntakeResult
    entities: dict[str, list[str]] = field(default_factory=dict)
    unknown_identifiers: list[dict[str, Any]] = field(default_factory=list)
    resolved_from_memory: list[str] = field(default_factory=list)
    extracted_by: str = "llm"

    def as_request(self) -> dict[str, Any]:
        """The serialised form stored in ``state["request"]``."""
        payload = self.result.model_dump()
        payload.update(
            extracted_by=self.extracted_by,
            unknown_identifiers=self.unknown_identifiers,
            resolved_from_memory=self.resolved_from_memory,
            prompt_version=prompt_version("intake"),
        )
        return payload


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _llm_intake(user_request: str, history: list[Any]) -> IntakeResult | None:
    """Ask the model for the structured reading. Returns None on any failure."""
    # Only the recent turns are needed to resolve references, and trimming keeps
    # this call - which runs on every turn - cheap.
    messages = [
        SystemMessage(content=INTAKE_PROMPT),
        *history[-6:],
        HumanMessage(content=f"Current request:\n{user_request}"),
    ]
    try:
        model = get_structured_llm("intake").with_structured_output(
            IntakeResult
        ).with_config({"run_name": "classify request"})
        result = model.invoke(messages)
    except Exception:  # noqa: BLE001 - degrade to the deterministic path
        return None
    if isinstance(result, IntakeResult):
        return result
    if isinstance(result, dict):
        try:
            return IntakeResult(**result)
        except Exception:  # noqa: BLE001
            return None
    return None


def _regex_intake(text: str) -> IntakeResult:
    """Deterministic fallback reading of the request."""
    identifiers = extract_identifiers(text)
    return IntakeResult(
        incident_type=classify_by_keyword(text),  # type: ignore[arg-type]
        intent=(text or "").strip()[:280] or "unspecified request",
        shipment_ids=identifiers.get("shipment_ids", []),
        supplier_ids=identifiers.get("supplier_ids", []),
        skus=identifiers.get("skus", []),
        warehouse_ids=identifiers.get("warehouse_ids", []),
        order_ids=identifiers.get("order_ids", []),
        incident_ids=identifiers.get("incident_ids", []),
        quantities=extract_quantities(text),
        missing_information=[],
        is_supported=True,
    )


def _merge_identifiers(
    result: IntakeResult, extracted: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Union of what the model reported and what the text demonstrably contains.

    The model occasionally paraphrases an id or drops one; the regex never
    invents one. Taking the union of both, then validating, is more reliable
    than trusting either alone.
    """
    merged: dict[str, list[str]] = {}
    for field_name in ENTITY_FIELDS:
        from_model = [
            value.strip().upper()
            for value in (getattr(result, field_name, None) or [])
            if isinstance(value, str) and value.strip()
        ]
        # Re-canonicalise the model's spelling through the same normaliser.
        spec = SPECS_BY_FIELD.get(field_name)
        if spec is not None:
            normalised: list[str] = []
            for value in from_model:
                candidates = spec.find(value)
                normalised.extend(candidates or [value])
            from_model = normalised
        for value in [*from_model, *(extracted.get(field_name) or [])]:
            if value not in merged.setdefault(field_name, []):
                merged[field_name].append(value)
    return {k: v for k, v in merged.items() if v}


@traced("resolve from entity memory")
def _carry_forward(
    identifiers: dict[str, list[str]],
    entities: dict[str, list[str]] | None,
    text: str,
) -> tuple[dict[str, list[str]], list[str]]:
    """Fill empty identifier fields from entity memory for referring requests."""
    if not entities or not REFERRING_EXPRESSIONS.search(text or ""):
        return identifiers, []

    resolved: list[str] = []
    enriched = dict(identifiers)
    for field_name in ENTITY_FIELDS:
        if enriched.get(field_name):
            continue
        remembered = most_recent(entities, field_name)
        if remembered:
            enriched[field_name] = [remembered]
            resolved.append(field_name)
    return enriched, resolved


def analyse_request(
    user_request: str,
    history: list[Any] | None = None,
    entities: dict[str, list[str]] | None = None,
) -> IntakeOutcome:
    """Read one request. Never raises; always returns a routable outcome."""
    history = history or []
    text = user_request or ""

    result = _llm_intake(text, history)
    extracted_by = "llm"
    if result is None:
        result = _regex_intake(text)
        extracted_by = "regex"

    identifiers = _merge_identifiers(result, extract_identifiers(text))
    identifiers, resolved_from_memory = _carry_forward(identifiers, entities, text)
    identifiers, unknown = validate_identifiers(identifiers)

    # Write the cleaned identifiers back onto the result the agents will see.
    for field_name in ENTITY_FIELDS:
        setattr(result, field_name, identifiers.get(field_name, []))
    if not result.quantities:
        result.quantities = extract_quantities(text)

    missing = list(result.missing_information or [])
    for entry in unknown:
        suggestion = (
            f" Did you mean {', '.join(entry['suggestions'])}?"
            if entry["suggestions"]
            else ""
        )
        note = f"{entry['value']} does not exist in our systems.{suggestion}"
        if note not in missing:
            missing.append(note)
    result.missing_information = missing

    return IntakeOutcome(
        result=result,
        entities=update_entities(entities, identifiers),
        unknown_identifiers=unknown,
        resolved_from_memory=resolved_from_memory,
        extracted_by=extracted_by,
    )
