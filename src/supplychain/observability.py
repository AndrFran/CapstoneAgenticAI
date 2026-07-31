"""LangSmith tracing setup.

The brief requires every team to enable tracing, record conversations, analyse
latency and review failed runs. LangSmith reads its configuration from
environment variables, so this module's job is to translate our
``SUPPLYCHAIN_*``/``LANGSMITH_*`` settings into the variables the LangChain
tracer expects, and to expose a small helper for tagging runs.

Owner: shared (all four team members).
"""

from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

from langsmith import traceable

from .config import get_settings
from .prompts import prompt_versions

F = TypeVar("F", bound=Callable[..., Any])

# How much of the operator's question goes into the run name.
RUN_NAME_LIMIT = 60


def traced(name: str, run_type: str = "chain", **kwargs: Any) -> Callable[[F], F]:
    """Make a plain Python function a first-class step in the trace.

    LangChain traces model calls and tools automatically, and LangGraph traces
    nodes - but everything deterministic in between is invisible, which in this
    codebase is most of the interesting logic: identifier normalisation,
    validation against the data layer, the routing clamps, proposal extraction
    and the write itself. A trace that shows only the model calls suggests the
    model is doing work it is not.

    A no-op when tracing is off, so this costs nothing in tests or on a
    key-less run.
    """

    def decorate(func: F) -> F:
        return traceable(name=name, run_type=run_type, **kwargs)(func)  # type: ignore[return-value]

    return decorate


def run_name(user_message: str) -> str:
    """A scannable name for the root run.

    LangGraph names every root run "LangGraph", so a project list is a column
    of identical rows and there is nothing to tell you which one to open.
    """
    text = " ".join((user_message or "").split())
    if not text:
        return "turn"
    if len(text) > RUN_NAME_LIMIT:
        text = text[: RUN_NAME_LIMIT - 1].rstrip() + "…"
    return text


def configure_tracing() -> bool:
    """Enable LangSmith tracing if a key is present. Returns True when active.

    Safe to call repeatedly (the Streamlit app calls it on every rerun).
    """
    settings = get_settings()

    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        # Make the disabled state explicit so a stale env var can't half-enable
        # the tracer and emit warnings on every call.
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
        return False

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ.setdefault(
        "LANGSMITH_ENDPOINT",
        os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
    )
    # Older LangChain versions read the LANGCHAIN_* aliases.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    return True


def tracing_status() -> dict[str, Any]:
    """Human-readable tracing status for the UI sidebar."""
    settings = get_settings()
    active = bool(settings.langsmith_tracing and settings.langsmith_api_key)
    return {
        "active": active,
        "project": settings.langsmith_project if active else None,
        "reason": (
            None
            if active
            else (
                "LANGSMITH_TRACING is false"
                if not settings.langsmith_tracing
                else "LANGSMITH_API_KEY is not set"
            )
        ),
    }


def run_config(
    thread_id: str,
    *,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Build the ``RunnableConfig`` passed into every graph invocation.

    ``thread_id`` drives LangGraph checkpointing (conversation memory) and also
    becomes LangSmith metadata, which is how you correlate a traced run back to
    a specific conversation in the UI.
    """
    settings = get_settings()
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": max(25, settings.max_hops * 4),
        # Without this every root run is called "LangGraph".
        **({"run_name": name} if name else {}),
        "tags": ["novaretail-supplychain", *(tags or [])],
        "metadata": {
            "thread_id": thread_id,
            "provider": "google_genai",
            "model": settings.model,
            "reasoning_effort": settings.reasoning_effort,
            "router_reasoning_effort": settings.router_reasoning_effort,
            "data_source": "rest_api" if settings.uses_rest_api else "json_fixtures",
            # Prompt versions, so a trace can be attributed to the prompt that
            # produced it and two versions can be compared in LangSmith.
            **{
                f"prompt_{name}": version
                for name, version in prompt_versions().items()
            },
            **(metadata or {}),
        },
    }
