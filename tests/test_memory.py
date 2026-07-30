"""Conversation memory and history tests.

Covers both back ends: the in-process store the tests use, and the SQLite store
that persists conversations across restarts.

Owner: Team Member 1.
"""

from __future__ import annotations

import sqlite3

import pytest

from supplychain import config, memory


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def test_title_is_derived_from_the_first_message():
    assert memory.derive_title("  What's the status of SHP-2026-0002?  ") == (
        "What's the status of SHP-2026-0002?"
    )


def test_long_titles_are_truncated():
    title = memory.derive_title("word " * 100)
    assert len(title) <= memory.MAX_TITLE_LENGTH
    assert title.endswith("…")


def test_empty_message_gets_a_placeholder_title():
    assert memory.derive_title("") == "New conversation"
    assert memory.derive_title("   ") == "New conversation"


# ---------------------------------------------------------------------------
# Store behaviour, exercised against both implementations
# ---------------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield memory.InMemoryConversationStore()
    else:
        connection = sqlite3.connect(
            str(tmp_path / "conversations.sqlite"), check_same_thread=False
        )
        yield memory.SqliteConversationStore(connection)
        connection.close()


def test_upsert_creates_then_updates(store):
    created = store.upsert("t1", title="First question")
    assert created.thread_id == "t1"
    assert created.title == "First question"
    assert created.turns == 0

    # A later upsert without a title must not clobber the existing one.
    again = store.upsert("t1")
    assert again.title == "First question"


def test_record_turn_increments_and_stores_severity(store):
    store.upsert("t1", title="Delay")
    store.record_turn("t1", severity="high")
    store.record_turn("t1")

    row = store.get("t1")
    assert row.turns == 2
    # A turn with no severity must not erase the last known one.
    assert row.last_severity == "high"


def test_record_turn_on_an_unknown_thread_is_a_no_op(store):
    store.record_turn("missing")
    assert store.get("missing") is None


def test_rename(store):
    store.upsert("t1", title="Original")
    store.rename("t1", "  A much better title  ")
    assert store.get("t1").title == "A much better title"


def test_delete(store):
    store.upsert("t1", title="Doomed")
    store.delete("t1")
    assert store.get("t1") is None
    assert store.list() == []


def test_delete_is_idempotent(store):
    store.delete("never-existed")


def test_list_is_most_recently_updated_first(store):
    store.upsert("older", title="Older")
    store.upsert("newer", title="Newer")
    store.record_turn("older")  # bumps updated_at
    ids = [row.thread_id for row in store.list()]
    assert ids[0] == "older"
    assert set(ids) == {"older", "newer"}


def test_list_respects_the_limit(store):
    for index in range(5):
        store.upsert(f"t{index}", title=f"Conversation {index}")
    assert len(store.list(limit=2)) == 2


def test_unknown_thread_returns_none(store):
    assert store.get("nope") is None


# ---------------------------------------------------------------------------
# SQLite specifics
# ---------------------------------------------------------------------------


def test_sqlite_store_survives_a_reconnect(tmp_path):
    path = str(tmp_path / "conversations.sqlite")

    first = sqlite3.connect(path, check_same_thread=False)
    memory.SqliteConversationStore(first).upsert("t1", title="Persisted")
    first.close()

    second = sqlite3.connect(path, check_same_thread=False)
    reopened = memory.SqliteConversationStore(second)
    assert reopened.get("t1").title == "Persisted"
    second.close()


def test_sqlite_delete_also_drops_checkpoint_rows(tmp_path):
    """Deleting a conversation must delete its state, not just its index row."""
    path = str(tmp_path / "conversations.sqlite")
    connection = sqlite3.connect(path, check_same_thread=False)

    # Stand in for a LangGraph checkpoint table: the store discovers any table
    # with a thread_id column rather than hard-coding schema names.
    connection.execute("CREATE TABLE checkpoints (thread_id TEXT, blob TEXT)")
    connection.execute("INSERT INTO checkpoints VALUES ('t1', 'state')")
    connection.execute("INSERT INTO checkpoints VALUES ('t2', 'other')")
    connection.commit()

    store = memory.SqliteConversationStore(connection)
    store.upsert("t1", title="Doomed")
    store.delete("t1")

    remaining = connection.execute("SELECT thread_id FROM checkpoints").fetchall()
    assert remaining == [("t2",)]
    connection.close()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_tests_run_against_the_in_process_backend():
    # conftest pins this, so a test run never writes the developer's history.
    assert memory.backend_name() == "memory"
    assert isinstance(memory.get_store(), memory.InMemoryConversationStore)


def test_sqlite_backend_is_selected_from_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_PATH", str(tmp_path / "history.sqlite"))
    config.reload_settings()
    memory.reset()

    assert memory.backend_name() == "sqlite"
    store = memory.get_store()
    store.upsert("t1", title="Durable")
    assert store.get("t1").title == "Durable"
    assert (tmp_path / "history.sqlite").exists()

    memory.reset()


def test_an_unwritable_memory_path_degrades_instead_of_crashing(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("i am a file", encoding="utf-8")

    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_PATH", str(blocker / "history.sqlite"))
    config.reload_settings()
    memory.reset()

    # Falls back to in-process memory rather than taking the app down.
    assert memory.backend_name() == "memory"
    memory.reset()


def test_invalid_backend_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("SUPPLYCHAIN_MEMORY_BACKEND", "postgres")
    assert config.reload_settings().memory_backend == "sqlite"
