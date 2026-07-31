"""Worker-agent factory.

Every domain agent is a ReAct loop over its own tool list: same wiring, one
prompt and one tool list per agent. Building them here means the graph file
stays about routing, and each engineer only touches their prompt and tools.

Agents are built lazily and cached, so importing the package (and compiling the
graph) does not require an API key - only invoking an agent does.

Owner: Team Member 4, used by everyone.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterable

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from ..llm import get_llm
from ..prompts import PROMPTS
from ..resilience import call_with_retry
from ..tools import (
    INCIDENT_TOOLS,
    INVENTORY_TOOLS,
    PROPOSAL_TOOL_NAMES,
    RECOVERY_TOOLS,
    SHIPMENT_TOOLS,
    SUPPLIER_TOOLS,
)
from ..tools.supplier import get_supplier_details

# The brief gives the Incident Analysis Agent get_supplier_details as well.
_INCIDENT_AGENT_TOOLS = [*INCIDENT_TOOLS, get_supplier_details]

AGENT_TOOLS: dict[str, list[BaseTool]] = {
    "incident_analysis": _INCIDENT_AGENT_TOOLS,
    "shipment": SHIPMENT_TOOLS,
    "inventory": INVENTORY_TOOLS,
    "supplier": SUPPLIER_TOOLS,
    "recovery": RECOVERY_TOOLS,
}


def text_of(message: BaseMessage | None) -> str:
    """Flatten a message's content to plain text.

    Claude returns a list of content blocks when thinking is enabled, so a bare
    ``message.content`` is not always a string.
    """
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def last_ai_text(messages: Iterable[AnyMessage]) -> str:
    """Text of the final AI message that is not a tool call."""
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = text_of(message)
            if text:
                return text
    return ""


def tool_calls_in(messages: Iterable[AnyMessage]) -> list[dict[str, Any]]:
    """Every tool call made during an agent run, in order."""
    calls: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                calls.append(
                    {
                        "name": call.get("name"),
                        "args": call.get("args", {}),
                        "id": call.get("id"),
                    }
                )
    return calls


def tool_results_in(messages: Iterable[AnyMessage]) -> list[dict[str, Any]]:
    """Every tool result from an agent run, paired with its tool name."""
    names: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                if call.get("id"):
                    names[call["id"]] = call.get("name", "")
        elif isinstance(message, ToolMessage):
            results.append(
                {
                    "name": message.name or names.get(message.tool_call_id, ""),
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
    return results


def first_tool_result(messages: Iterable[AnyMessage], tool_name: str) -> Any | None:
    """Parsed JSON of the first result from a named tool, if it was called."""
    for result in tool_results_in(messages):
        if result["name"] == tool_name:
            content = result["content"]
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    return None
            return content
    return None


def find_pending_proposal(messages: Iterable[AnyMessage]) -> dict[str, Any] | None:
    """Return the last write-action proposal an agent made, if any.

    The Recovery Agent proposes writes with ``propose_*`` tools. Rather than
    letting a tool mutate shared state, the graph reads the proposal back out
    of the agent's own tool calls - so the human-in-the-loop gate cannot be
    bypassed by the model.
    """
    proposal = None
    for call in tool_calls_in(messages):
        if call["name"] in PROPOSAL_TOOL_NAMES:
            proposal = {
                "action": {
                    "propose_incident": "create_incident",
                    "propose_reroute": "reroute_shipment",
                    "propose_escalation": "escalate_incident",
                }[call["name"]],
                "payload": call["args"],
                "proposed_by": "recovery",
                "tool_call_id": call["id"],
            }
    return proposal


@lru_cache(maxsize=8)
def get_worker(name: str):
    """Build (and cache) the compiled tool-calling agent for one domain agent.

    Uses LangChain 1.x ``create_agent`` (the current successor to
    ``langgraph.prebuilt.create_react_agent``), which returns a compiled
    LangGraph so the worker can be invoked as a unit from the outer graph.
    """
    if name not in AGENT_TOOLS:
        raise KeyError(f"Unknown agent {name!r}. Known: {sorted(AGENT_TOOLS)}")
    return create_agent(
        get_llm(name),
        AGENT_TOOLS[name],
        system_prompt=PROMPTS[name],
        name=name,
    )


def reset_worker_cache() -> None:
    get_worker.cache_clear()


def build_context_block(state: dict[str, Any]) -> str:
    """Render the shared state into the briefing a worker agent receives.

    Workers do not see the raw conversation - they see the intake summary and
    what the other agents have already established. This keeps each agent's
    trace focused and its token cost predictable.
    """
    lines: list[str] = []
    request = state.get("request") or {}
    if request:
        lines.append("Intake summary:")
        lines.append(f"  incident_type: {request.get('incident_type')}")
        lines.append(f"  intent: {request.get('intent')}")
        for field, label in (
            ("shipment_ids", "shipments"),
            ("supplier_ids", "suppliers"),
            ("skus", "skus"),
            ("warehouse_ids", "warehouses"),
            ("order_ids", "orders"),
            ("route_ids", "routes"),
            ("incident_ids", "incidents"),
            ("quantities", "quantities"),
        ):
            values = request.get(field) or []
            if values:
                lines.append(f"  {label}: {', '.join(str(v) for v in values)}")
        if request.get("resolved_from_memory"):
            lines.append(
                "  carried forward from earlier in the conversation: "
                + ", ".join(request["resolved_from_memory"])
            )
        if request.get("missing_information"):
            lines.append(
                "  missing: " + "; ".join(request["missing_information"])
            )

    if state.get("severity"):
        lines.append(f"Assessed severity: {state['severity']}")

    findings = state.get("findings") or {}
    if findings:
        lines.append("")
        lines.append("What other agents have established so far:")
        for agent, finding in findings.items():
            summary = finding.get("summary") if isinstance(finding, dict) else finding
            if summary:
                lines.append(f"  [{agent}] {summary}")

    return "\n".join(lines).strip()


def run_worker(name: str, state: dict[str, Any], task: str) -> dict[str, Any]:
    """Invoke a worker agent and return its findings.

    Returns a dict with the agent's summary text, the tool calls it made, any
    pending write proposal, and its raw messages (kept for the UI's trace view).
    """
    agent = get_worker(name)

    context = build_context_block(state)
    user_request = state.get("user_request", "")
    briefing = "\n\n".join(
        part
        for part in (
            f"Operator request:\n{user_request}" if user_request else "",
            f"Your task:\n{task}" if task else "",
            f"Context:\n{context}" if context else "",
        )
        if part
    )

    # A worker is the longest model call in the turn and the most likely to hit
    # a rate limit. Waiting one quota window out is far cheaper than losing the
    # work every other agent has already done.
    result = call_with_retry(
        lambda: agent.invoke({"messages": [HumanMessage(content=briefing)]}),
        label=f"{name} agent",
    )
    messages = result.get("messages", [])

    classification = first_tool_result(messages, "classify_incident_severity") or {}

    return {
        "agent": name,
        "summary": last_ai_text(messages) or "(no summary returned)",
        "tool_calls": tool_calls_in(messages),
        "tool_results": tool_results_in(messages),
        "severity": classification.get("severity"),
        "pending_action": find_pending_proposal(messages),
        "messages": messages,
    }
