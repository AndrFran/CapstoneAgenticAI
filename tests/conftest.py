"""Pytest configuration.

Adds ``src`` to the path so the tests run against the source tree without an
install step, keeps the runtime write store out of the committed fixtures, and
pins conversation memory to the in-process back end so no test writes to the
developer's SQLite history file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from supplychain import config, llm, memory, runner  # noqa: E402
from supplychain.data import access  # noqa: E402

# Credentials that would let an agent make a live model call. A developer's
# .env (API key or Vertex AI settings) must never leak into the suite - the
# tests are deterministic and key-free by design.
LLM_CREDENTIAL_VARS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


@pytest.fixture(autouse=True)
def no_llm_credentials(monkeypatch):
    """Every test runs unconfigured unless it sets credentials itself."""
    for name in LLM_CREDENTIAL_VARS:
        monkeypatch.delenv(name, raising=False)
    config.reload_settings()
    llm.reset_llm_cache()
    yield
    config.reload_settings()
    llm.reset_llm_cache()


@pytest.fixture(autouse=True)
def clean_runtime_store():
    """Every test starts from the committed fixtures, with no runtime writes."""
    access.reset_runtime_store()
    yield
    access.reset_runtime_store()


@pytest.fixture(autouse=True)
def in_process_memory(monkeypatch):
    """Never touch the real conversation history during tests."""
    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_BACKEND", "memory")
    config.reload_settings()
    memory.reset()
    runner.reset_graph()
    yield
    memory.reset()
    runner.reset_graph()
    config.reload_settings()
