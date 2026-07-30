"""Provider-schema tests.

Gemini's function-calling schema is stricter than some other providers' about
parameter types: nested containers and exotic unions do not survive conversion.
Nothing catches that at bind time — ``bind_tools`` converts lazily, so a bad
signature only fails on the first real request.

Two guards here:

1. Every tool parameter annotation is a type Gemini can express (public, stable,
   independent of library internals).
2. Every tool and both structured-output schemas actually convert through
   langchain-google-genai's converter.
"""

from __future__ import annotations

import types
import typing

import pytest

from supplychain.state import IntakeResult, RouteDecision
from supplychain.tools import ALL_TOOLS

# Scalars Gemini maps cleanly, plus one level of list-of-scalar. Optional /
# `| None` variants of these are fine too - they become `nullable` fields.
ALLOWED_SCALARS = {str, int, float, bool}
ALLOWED_SEQUENCES = {list[str], list[int], list[float]}


def _strip_optional(annotation: typing.Any) -> typing.Any:
    """Reduce ``X | None`` / ``Optional[X]`` to ``X``.

    Covers both spellings: ``typing.Optional`` and the PEP 604 ``X | None``,
    whose origin is ``types.UnionType`` rather than ``typing.Union``.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _annotations_of(tool) -> dict[str, typing.Any]:
    """Parameter annotations for a LangChain tool, from its args schema."""
    schema = tool.get_input_schema()
    return {name: field.annotation for name, field in schema.model_fields.items()}


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_tool_parameters_use_gemini_expressible_types(tool):
    for name, annotation in _annotations_of(tool).items():
        base = _strip_optional(annotation)
        assert base in ALLOWED_SCALARS or base in ALLOWED_SEQUENCES, (
            f"{tool.name}.{name} is annotated {annotation!r}, which Gemini's "
            "function-calling schema cannot express. Use a scalar or a "
            "list of scalars."
        )


def _converter():
    module = pytest.importorskip(
        "langchain_google_genai._function_utils",
        reason="langchain-google-genai internals moved; the type allowlist test still applies",
    )
    return module.convert_to_genai_function_declarations


def _declarations(obj):
    converted = _converter()([obj])
    if isinstance(converted, list):
        out = []
        for entry in converted:
            nested = getattr(entry, "function_declarations", None)
            out.extend(nested if nested else [entry])
        return out
    return list(converted.function_declarations)


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.name)
def test_every_tool_converts_to_a_gemini_declaration(tool):
    declarations = _declarations(tool)
    assert len(declarations) == 1

    declaration = declarations[0]
    assert declaration.name == tool.name
    assert declaration.description

    expected = set(_annotations_of(tool))
    if not expected:
        return
    params = declaration.parameters
    assert params is not None, f"{tool.name} lost its parameters in conversion"
    assert set(params.properties or {}) == expected


@pytest.mark.parametrize(
    "schema", [IntakeResult, RouteDecision], ids=["IntakeResult", "RouteDecision"]
)
def test_structured_output_schemas_convert(schema):
    declaration = _declarations(schema)[0]
    assert set(declaration.parameters.properties or {}) == set(schema.model_fields)


def test_optional_parameters_become_nullable_rather_than_required():
    declaration = _declarations(
        next(t for t in ALL_TOOLS if t.name == "identify_delayed_shipments")
    )[0]
    assert not (declaration.parameters.required or [])
    assert declaration.parameters.properties["warehouse_id"].nullable is True


def test_list_parameters_convert_to_arrays_of_strings():
    declaration = _declarations(
        next(t for t in ALL_TOOLS if t.name == "compare_supplier_options")
    )[0]
    supplier_ids = declaration.parameters.properties["supplier_ids"]
    assert getattr(supplier_ids.type, "name", str(supplier_ids.type)).upper().endswith(
        "ARRAY"
    )
    assert getattr(supplier_ids.items.type, "name", "").upper().endswith("STRING")
