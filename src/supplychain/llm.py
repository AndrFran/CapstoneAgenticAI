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


def _require_key() -> str:
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMNotConfiguredError(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and add a key "
            "from https://aistudio.google.com/apikey"
        )
    return settings.google_api_key  # type: ignore[return-value]


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
        google_api_key=_require_key(),
        max_output_tokens=settings.max_output_tokens,
        # Maps to Gemini's `thinking_level`.
        reasoning_effort=settings.reasoning_effort,
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
        google_api_key=_require_key(),
        max_output_tokens=min(settings.max_output_tokens, 2048),
        reasoning_effort=settings.router_reasoning_effort,
    )


def reset_llm_cache() -> None:
    """Clear the cached models (used after settings change at runtime)."""
    get_llm.cache_clear()
    get_structured_llm.cache_clear()
