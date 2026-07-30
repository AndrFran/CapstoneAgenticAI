"""Streamlit UI tests using Streamlit's own AppTest harness.

Runs the real app script in-process with real session state, so the chat flow,
the sample prompts and the no-API-key path are covered without a browser and
without an API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


@pytest.fixture
def no_api_key():
    """Run the app as if no Google AI credentials were configured.

    conftest's autouse `no_llm_credentials` fixture already strips every
    credential variable (API key and Vertex AI alike) and rebuilds the cached
    settings, so this is just a readable name for that state at the call site.
    """
    yield


def fresh_app():
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    return app


def test_app_renders_without_an_api_key(no_api_key):
    app = fresh_app()
    assert not app.exception
    assert "Supply Chain Disruption Management" in app.title[0].value
    # The sidebar warns about the missing key rather than crashing.
    assert any("GOOGLE_API_KEY" in err.value for err in app.error)


def test_sidebar_reports_the_data_layer(no_api_key):
    app = fresh_app()
    assert any("json_fixtures" in info.value for info in app.info)
    assert any("28 shipments" in info.value for info in app.info)


def test_every_sample_prompt_has_a_button(no_api_key):
    app = fresh_app()
    labels = {button.label for button in app.button}
    assert any("New conversation" in label for label in labels)
    assert len(labels) >= 7  # six samples plus New conversation


def test_chat_turn_without_a_key_answers_instead_of_crashing(no_api_key):
    app = fresh_app()

    app.chat_input[0].set_value("What's the status of shipment SHP-2026-0002?").run()
    assert not app.exception

    roles = [entry["role"] for entry in app.session_state["history"]]
    assert roles == ["user", "assistant"]
    assert "GOOGLE_API_KEY" in app.session_state["history"][1]["content"]


def test_new_conversation_starts_a_fresh_thread(no_api_key):
    app = fresh_app()
    app.chat_input[0].set_value("Which shipments are delayed?").run()
    assert app.session_state["history"]

    thread_before = app.session_state["thread_id"]
    next(b for b in app.button if "New conversation" in b.label).click().run()

    assert app.session_state["history"] == []
    assert app.session_state["thread_id"] != thread_before
    assert not app.exception


def test_sidebar_reports_the_memory_backend(no_api_key):
    app = fresh_app()
    captions = " ".join(caption.value for caption in app.caption)
    assert "Memory:" in captions
    assert "in-process only" in captions  # conftest pins the memory back end


def test_sample_prompt_button_starts_a_turn(no_api_key):
    app = fresh_app()

    sample = next(
        button for button in app.button if button.label.startswith("What's the status")
    )
    sample.click().run()

    assert not app.exception
    assert app.session_state["history"][0]["role"] == "user"
    assert app.session_state["history"][0]["content"].startswith("What's the status")
