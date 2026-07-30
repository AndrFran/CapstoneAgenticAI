"""Central configuration, loaded from environment variables.

Every tunable lives here so no module reads ``os.environ`` directly. See
``.env.example`` for the full list.

Owner: shared (all four team members).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]
MOCK_DATA_DIR = PACKAGE_ROOT / "data" / "mock"
RUNTIME_DATA_DIR = PACKAGE_ROOT / "data" / "runtime"

# Load .env once, at import time. Values already present in the real
# environment win, which is what Streamlit Community Cloud secrets rely on.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# Gemini's thinking depth control (`thinking_level` on the wire) is exposed by
# langchain-google-genai as `reasoning_effort`.
REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _effort(name: str, default: str) -> str:
    raw = (os.getenv(name) or "").strip().lower()
    return raw if raw in REASONING_EFFORTS else default


def _google_api_key() -> str | None:
    """Google AI Studio key.

    ``GOOGLE_API_KEY`` is what langchain-google-genai reads by default;
    ``GEMINI_API_KEY`` is what the google-genai SDK and most Google samples use,
    so both are accepted.
    """
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or None


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the runtime configuration."""

    # LLM (Google AI / Gemini)
    google_api_key: str | None = field(default_factory=_google_api_key)
    # Vertex AI backend: langchain-google-genai 4.x selects Vertex from these
    # standard google-genai env vars and authenticates with gcloud ADC, so no
    # API key is needed. GOOGLE_CLOUD_LOCATION (default "global") is read by
    # the SDK itself.
    use_vertexai: bool = field(
        default_factory=lambda: _bool("GOOGLE_GENAI_USE_VERTEXAI", False)
    )
    google_cloud_project: str | None = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT") or None
    )
    model: str = field(
        default_factory=lambda: os.getenv("SUPPLYCHAIN_MODEL", "gemini-3.1-flash-lite")
    )
    reasoning_effort: str = field(
        default_factory=lambda: _effort("SUPPLYCHAIN_REASONING_EFFORT", "medium")
    )
    router_reasoning_effort: str = field(
        default_factory=lambda: _effort("SUPPLYCHAIN_ROUTER_REASONING_EFFORT", "low")
    )
    max_output_tokens: int = field(
        default_factory=lambda: _int("SUPPLYCHAIN_MAX_OUTPUT_TOKENS", 4096)
    )

    # Observability
    langsmith_tracing: bool = field(
        default_factory=lambda: _bool("LANGSMITH_TRACING", False)
    )
    langsmith_api_key: str | None = field(
        default_factory=lambda: os.getenv("LANGSMITH_API_KEY") or None
    )
    langsmith_project: str = field(
        default_factory=lambda: os.getenv(
            "LANGSMITH_PROJECT", "novaretail-supplychain"
        )
    )

    # Data layer
    api_base_url: str | None = field(
        default_factory=lambda: (os.getenv("SUPPLYCHAIN_API_BASE_URL") or "").rstrip("/")
        or None
    )

    # Workflow
    max_hops: int = field(default_factory=lambda: _int("SUPPLYCHAIN_MAX_HOPS", 8))
    require_approval: bool = field(
        default_factory=lambda: _bool("SUPPLYCHAIN_REQUIRE_APPROVAL", True)
    )

    @property
    def llm_configured(self) -> bool:
        if self.use_vertexai:
            return bool(self.google_cloud_project)
        return bool(self.google_api_key)

    @property
    def uses_rest_api(self) -> bool:
        return self.api_base_url is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings snapshot."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache and re-read the environment (used by tests and the UI)."""
    get_settings.cache_clear()
    return get_settings()
