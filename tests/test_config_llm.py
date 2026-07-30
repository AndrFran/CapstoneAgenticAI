"""Configuration and model-factory tests.

Covers the Google AI wiring without making a network call: key resolution,
reasoning-effort validation, and the failure mode when no key is configured.
"""

from __future__ import annotations

import pytest

from supplychain import config, llm


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Each test gets a clean settings snapshot and model cache."""
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "SUPPLYCHAIN_MODEL",
        "SUPPLYCHAIN_REASONING_EFFORT",
        "SUPPLYCHAIN_ROUTER_REASONING_EFFORT",
        "SUPPLYCHAIN_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reload_settings()
    llm.reset_llm_cache()
    yield
    config.reload_settings()
    llm.reset_llm_cache()


def test_defaults():
    settings = config.get_settings()
    assert settings.model == "gemini-3.1-flash-lite"
    assert settings.reasoning_effort == "medium"
    assert settings.router_reasoning_effort == "low"
    assert settings.max_output_tokens == 4096
    assert settings.llm_configured is False


def test_gemini_api_key_is_accepted_as_an_alias(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    settings = config.reload_settings()
    assert settings.google_api_key == "test-key"
    assert settings.llm_configured is True


def test_google_api_key_wins_over_the_alias(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY", "alias")
    assert config.reload_settings().google_api_key == "primary"


def test_invalid_reasoning_effort_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("SUPPLYCHAIN_REASONING_EFFORT", "ludicrous")
    assert config.reload_settings().reasoning_effort == "medium"


@pytest.mark.parametrize("effort", config.REASONING_EFFORTS)
def test_every_valid_reasoning_effort_is_accepted(monkeypatch, effort):
    monkeypatch.setenv("SUPPLYCHAIN_REASONING_EFFORT", effort)
    assert config.reload_settings().reasoning_effort == effort


def test_reasoning_effort_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("SUPPLYCHAIN_REASONING_EFFORT", "HIGH")
    assert config.reload_settings().reasoning_effort == "high"


def test_get_llm_raises_a_clear_error_without_a_key():
    with pytest.raises(llm.LLMNotConfiguredError, match="GOOGLE_API_KEY"):
        llm.get_llm("test")
    with pytest.raises(llm.LLMNotConfiguredError, match="GOOGLE_API_KEY"):
        llm.get_structured_llm("test")


def test_models_are_built_against_google_ai(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("SUPPLYCHAIN_MODEL", "gemini-3.1-flash-lite")
    config.reload_settings()
    llm.reset_llm_cache()

    worker = llm.get_llm("worker")
    router = llm.get_structured_llm("router")

    from langchain_google_genai import ChatGoogleGenerativeAI

    assert isinstance(worker, ChatGoogleGenerativeAI)
    assert isinstance(router, ChatGoogleGenerativeAI)
    assert worker.model.endswith("gemini-3.1-flash-lite")
    # The router runs shallower and with a smaller output budget than a worker.
    assert worker.reasoning_effort == "medium"
    assert router.reasoning_effort == "low"
    assert router.max_output_tokens <= worker.max_output_tokens


def test_vertex_mode_is_configured_without_an_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    settings = config.reload_settings()
    assert settings.google_api_key is None
    assert settings.use_vertexai is True
    assert settings.llm_configured is True


def test_vertex_mode_without_a_project_is_not_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    settings = config.reload_settings()
    assert settings.llm_configured is False
    llm.reset_llm_cache()
    with pytest.raises(llm.LLMNotConfiguredError, match="GOOGLE_CLOUD_PROJECT"):
        llm.get_llm("test")


def test_vertex_models_build_without_an_api_key(monkeypatch):
    """Construction must not need credentials - ADC resolves at request time."""
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    config.reload_settings()
    llm.reset_llm_cache()

    from langchain_google_genai import ChatGoogleGenerativeAI

    worker = llm.get_llm("worker")
    assert isinstance(worker, ChatGoogleGenerativeAI)
    assert llm.get_structured_llm("router") is not worker


def test_gemini_2x_models_do_not_get_reasoning_effort(monkeypatch):
    """Gemini 2.x rejects `thinking_level` at request time, so it must be omitted."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("SUPPLYCHAIN_MODEL", "gemini-2.5-flash")
    config.reload_settings()
    llm.reset_llm_cache()
    assert llm.get_llm("worker").reasoning_effort is None
    assert llm.get_structured_llm("router").reasoning_effort is None


def test_models_are_cached_per_tag(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    config.reload_settings()
    llm.reset_llm_cache()
    assert llm.get_llm("shipment") is llm.get_llm("shipment")
    assert llm.get_llm("shipment") is not llm.get_llm("inventory")
