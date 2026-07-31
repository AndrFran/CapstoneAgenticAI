"""Final response generation.

The last node in the graph: combines every agent's findings into the single
answer the operator sees.

Owner: Team Member 4 (Recovery & Supervisor Agent Engineer).
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm import get_llm
from ..prompts import RESPONDER_PROMPT
from .base import text_of


def _brief(state: dict[str, Any]) -> str:
    request = state.get("request") or {}
    findings = state.get("findings") or {}
    lines = [f"Operator request:\n{state.get('user_request', '')}", ""]

    if request:
        lines.append(f"Interpreted as: {request.get('intent')} "
                     f"(type: {request.get('incident_type')})")
        if not request.get("is_supported", True):
            lines.append(
                "This request is outside supply chain operations - say so politely "
                "and describe what this assistant does cover."
            )
        if request.get("clarification_question"):
            lines.append(
                f"Blocked on missing information. Ask: {request['clarification_question']}"
            )
        if request.get("missing_information"):
            lines.append("Missing: " + "; ".join(request["missing_information"]))

    if state.get("severity"):
        lines.append(f"Assessed severity: {state['severity']}")

    if findings:
        lines.append("")
        lines.append("Agent findings:")
        for agent, finding in findings.items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            lines.append(f"\n[{agent}]\n{summary}")

    pending = state.get("pending_action")
    if pending:
        lines.append("")
        lines.append(
            f"Awaiting operator approval: {pending.get('action')} with payload "
            f"{pending.get('payload')}. It has NOT been applied yet."
        )

    executed = state.get("executed_actions") or []
    if executed:
        lines.append("")
        lines.append("Actions applied after approval:")
        for action in executed:
            lines.append(f"  {action.get('action')}: {action.get('result')}")

    if not findings and not pending and not executed:
        lines.append("")
        lines.append(
            "No agent findings are available. Answer from the intake summary alone "
            "and ask for what you need."
        )

    return "\n".join(lines)


def write_response(state: dict[str, Any]) -> str:
    """Generate the user-facing answer."""
    try:
        model = get_llm("responder").with_config({"run_name": "compose answer"})
        message = model.invoke(
            [
                SystemMessage(content=RESPONDER_PROMPT),
                HumanMessage(content=_brief(state)),
            ]
        )
        text = text_of(message)
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 - never fail the turn on the last step
        findings = state.get("findings") or {}
        if findings:
            joined = "\n\n".join(
                f"{agent}: {f.get('summary') if isinstance(f, dict) else f}"
                for agent, f in findings.items()
            )
            return (
                "I could not compose the final summary, but here is what the "
                f"analysis found:\n\n{joined}"
            )
        return f"Sorry - I could not complete that request ({exc.__class__.__name__})."

    return "I was not able to produce an answer for that request."
