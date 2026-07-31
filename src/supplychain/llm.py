"""LLM factory - Google AI (Gemini).

One place that knows how to build a chat model, so swapping the model or the
reasoning depth is a config change rather than a code change.

Built on LangChain 1.x's ``init_chat_model``, which is the current provider-
agnostic entry point: the model string and provider are data, so pointing this
at a different provider later means changing ``model_provider`` and the key,
not rewriting the agents.

Owner: shared (all four team members).
"""

from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from .config import get_settings

MODEL_PROVIDER = "google_genai"


class LLMNotConfiguredError(RuntimeError):
    """Raised when an agent is invoked without an API key present."""


def _credential_kwargs() -> dict:
    """Model kwargs for whichever Google auth path is configured.

    Two paths: an AI Studio API key (the default), or the Vertex AI backend
    selected via GOOGLE_GENAI_USE_VERTEXAI=true + GOOGLE_CLOUD_PROJECT and
    authenticated with gcloud ADC - no key required.
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMNotConfiguredError(
            "No LLM credentials configured. Either set GOOGLE_API_KEY (get one "
            "at https://aistudio.google.com/apikey), or use Vertex AI: set "
            "GOOGLE_GENAI_USE_VERTEXAI=true and GOOGLE_CLOUD_PROJECT, and log "
            "in with `gcloud auth application-default login`. See .env.example."
        )
    if settings.use_vertexai:
        return {"vertexai": True, "project": settings.google_cloud_project}
    return {"google_api_key": settings.google_api_key}


def _reasoning_kwargs(effort: str) -> dict:
    """Reasoning-depth kwargs, only for models that accept them.

    ``reasoning_effort`` maps to Gemini 3's ``thinking_level``; Gemini 2.x
    models reject it at request time, so omit it there rather than fail.
    """
    if get_settings().model.startswith("gemini-2"):
        return {}
    return {"reasoning_effort": effort}


@lru_cache(maxsize=8)
def get_llm(tag: str = "default") -> BaseChatModel:
    """Return a cached chat model for the worker agents and the responder.

    ``tag`` only gives callers separate cache slots (one per agent), so a future
    change can give an agent its own model without touching call sites.

    Temperature is deliberately not set: Google recommends leaving Gemini 3.x at
    its default sampling settings and controlling depth with reasoning effort
    instead.
    """
    settings = get_settings()
    return init_chat_model(
        settings.model,
        model_provider=MODEL_PROVIDER,
        max_output_tokens=settings.max_output_tokens,
        # Maps to Gemini's `thinking_level` (Gemini 3 only).
        **_reasoning_kwargs(settings.reasoning_effort),
        **_credential_kwargs(),
    )


@lru_cache(maxsize=4)
def get_structured_llm(tag: str = "router") -> BaseChatModel:
    """Model used for structured-output calls (intake extraction, routing).

    These are short classification/extraction steps on the critical path of
    every turn, so they run at a lower reasoning effort and a smaller output
    budget than the worker agents.
    """
    settings = get_settings()
    return init_chat_model(
        settings.model,
        model_provider=MODEL_PROVIDER,
        max_output_tokens=min(settings.max_output_tokens, 2048),
        **_reasoning_kwargs(settings.router_reasoning_effort),
        **_credential_kwargs(),
    )


def reset_llm_cache() -> None:
    """Clear the cached models (used after settings change at runtime)."""
    get_llm.cache_clear()
    get_structured_llm.cache_clear()
