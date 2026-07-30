"""Conversation memory and history.

Two distinct things the brief asks for, both landing here:

* **Memory** - the LangGraph checkpointer. It holds the message history and the
  workflow state for a ``thread_id``, which is what makes a follow-up question
  ("who is the supplier on that shipment?") answerable and what makes the
  human-in-the-loop interrupt resumable.
* **History** - a registry of past conversations, so the UI can list them,
  reopen one, rename it and delete it. LangGraph checkpointers store state per
  thread but have no notion of "which threads exist", so that index lives here.

Default back end is SQLite, so both survive an app restart. Set
``SUPPLYCHAIN_MEMORY_BACKEND=memory`` for an in-process-only run (what the tests
use).

Owner: Team Member 1 (conversation memory and history).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from .config import get_settings

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"checkpointer": None, "store": None, "connection": None}

MAX_TITLE_LENGTH = 80


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_title(text: str) -> str:
    """Turn the first user message into a conversation title."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "New conversation"
    if len(cleaned) <= MAX_TITLE_LENGTH:
        return cleaned
    return cleaned[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


@dataclass(frozen=True)
class Conversation:
    """One row of the conversation history index."""

    thread_id: str
    title: str
    created_at: str
    updated_at: str
    turns: int = 0
    last_severity: str | None = None


# ---------------------------------------------------------------------------
# History index
# ---------------------------------------------------------------------------


class ConversationStore:
    """Interface for the conversation history index."""

    def upsert(self, thread_id: str, *, title: str | None = None) -> Conversation:
        raise NotImplementedError

    def record_turn(self, thread_id: str, *, severity: str | None = None) -> None:
        raise NotImplementedError

    def rename(self, thread_id: str, title: str) -> None:
        raise NotImplementedError

    def delete(self, thread_id: str) -> None:
        raise NotImplementedError

    def get(self, thread_id: str) -> Conversation | None:
        raise NotImplementedError

    def list(self, limit: int = 50) -> list[Conversation]:
        raise NotImplementedError


class InMemoryConversationStore(ConversationStore):
    """Process-local index. Lost on restart, which is the point."""

    def __init__(self) -> None:
        self._rows: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    def upsert(self, thread_id: str, *, title: str | None = None) -> Conversation:
        with self._lock:
            existing = self._rows.get(thread_id)
            if existing is None:
                row = Conversation(
                    thread_id=thread_id,
                    title=title or "New conversation",
                    created_at=_now(),
                    updated_at=_now(),
                )
            else:
                row = Conversation(
                    thread_id=thread_id,
                    title=title or existing.title,
                    created_at=existing.created_at,
                    updated_at=_now(),
                    turns=existing.turns,
                    last_severity=existing.last_severity,
                )
            self._rows[thread_id] = row
            return row

    def record_turn(self, thread_id: str, *, severity: str | None = None) -> None:
        with self._lock:
            existing = self._rows.get(thread_id)
            if existing is None:
                return
            self._rows[thread_id] = Conversation(
                thread_id=thread_id,
                title=existing.title,
                created_at=existing.created_at,
                updated_at=_now(),
                turns=existing.turns + 1,
                last_severity=severity or existing.last_severity,
            )

    def rename(self, thread_id: str, title: str) -> None:
        self.upsert(thread_id, title=derive_title(title))

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._rows.pop(thread_id, None)

    def get(self, thread_id: str) -> Conversation | None:
        return self._rows.get(thread_id)

    def list(self, limit: int = 50) -> list[Conversation]:
        rows = sorted(self._rows.values(), key=lambda r: r.updated_at, reverse=True)
        return rows[:limit]


class SqliteConversationStore(ConversationStore):
    """Durable index, in the same database file as the checkpoints."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        thread_id     TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        turns         INTEGER NOT NULL DEFAULT 0,
        last_severity TEXT
    )
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(self.SCHEMA)
            self._conn.commit()

    def _row_to_conversation(self, row: tuple) -> Conversation:
        return Conversation(
            thread_id=row[0],
            title=row[1],
            created_at=row[2],
            updated_at=row[3],
            turns=row[4],
            last_severity=row[5],
        )

    def upsert(self, thread_id: str, *, title: str | None = None) -> Conversation:
        with self._lock:
            existing = self._conn.execute(
                "SELECT thread_id, title, created_at, updated_at, turns, last_severity "
                "FROM conversations WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    "INSERT INTO conversations (thread_id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (thread_id, title or "New conversation", _now(), _now()),
                )
            elif title:
                self._conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE thread_id = ?",
                    (title, _now(), thread_id),
                )
            else:
                self._conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE thread_id = ?",
                    (_now(), thread_id),
                )
            self._conn.commit()
        return self.get(thread_id)  # type: ignore[return-value]

    def record_turn(self, thread_id: str, *, severity: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET turns = turns + 1, updated_at = ?, "
                "last_severity = COALESCE(?, last_severity) WHERE thread_id = ?",
                (_now(), severity, thread_id),
            )
            self._conn.commit()

    def rename(self, thread_id: str, title: str) -> None:
        self.upsert(thread_id, title=derive_title(title))

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM conversations WHERE thread_id = ?", (thread_id,)
            )
            # Also drop the thread's checkpoints so a delete really deletes.
            # Table names are discovered rather than hard-coded, so this keeps
            # working if LangGraph changes its schema.
            for (table,) in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall():
                if table == "conversations":
                    continue
                columns = {
                    row[1]
                    for row in self._conn.execute(f"PRAGMA table_info('{table}')")
                }
                if "thread_id" in columns:
                    self._conn.execute(
                        f"DELETE FROM '{table}' WHERE thread_id = ?", (thread_id,)
                    )
            self._conn.commit()

    def get(self, thread_id: str) -> Conversation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT thread_id, title, created_at, updated_at, turns, last_severity "
                "FROM conversations WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def list(self, limit: int = 50) -> list[Conversation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT thread_id, title, created_at, updated_at, turns, last_severity "
                "FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_conversation(row) for row in rows]


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _build_sqlite() -> tuple[Any, ConversationStore, sqlite3.Connection]:
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings = get_settings()
    path = settings.memory_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because Streamlit reruns on a different thread.
    connection = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    return checkpointer, SqliteConversationStore(connection), connection


def _build_memory() -> tuple[Any, ConversationStore, None]:
    return MemorySaver(), InMemoryConversationStore(), None


def _ensure_built() -> None:
    if _STATE["checkpointer"] is not None:
        return
    settings = get_settings()
    if settings.persists_conversations:
        try:
            checkpointer, store, connection = _build_sqlite()
        except Exception:  # noqa: BLE001 - a read-only or missing disk must not
            # take the app down; degrade to in-process memory instead.
            checkpointer, store, connection = _build_memory()
    else:
        checkpointer, store, connection = _build_memory()
    _STATE.update(checkpointer=checkpointer, store=store, connection=connection)


def get_checkpointer() -> Any:
    """The process-wide checkpointer (conversation memory)."""
    with _LOCK:
        _ensure_built()
        return _STATE["checkpointer"]


def get_store() -> ConversationStore:
    """The process-wide conversation history index."""
    with _LOCK:
        _ensure_built()
        return _STATE["store"]


def backend_name() -> str:
    """Which back end actually got built (not merely requested)."""
    with _LOCK:
        _ensure_built()
        return (
            "memory"
            if isinstance(_STATE["store"], InMemoryConversationStore)
            else "sqlite"
        )


def reset() -> None:
    """Tear down memory and history (tests, and switching back end at runtime)."""
    with _LOCK:
        connection = _STATE.get("connection")
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
        _STATE.update(checkpointer=None, store=None, connection=None)


# ---------------------------------------------------------------------------
# Entity memory
# ---------------------------------------------------------------------------

ENTITY_FIELDS = (
    "shipment_ids",
    "supplier_ids",
    "skus",
    "warehouse_ids",
    "order_ids",
    "incident_ids",
    "route_ids",
)


def update_entities(
    entities: dict[str, list[str]] | None,
    mentioned: dict[str, list[str]],
    *,
    depth: int | None = None,
) -> dict[str, list[str]]:
    """Fold this turn's identifiers into the conversation's entity memory.

    Most-recently-mentioned first, de-duplicated, capped at ``depth`` per kind.
    This is what lets "that shipment" resolve on turn 3 - including on the
    regex fallback path, which has no model to reason with.
    """
    depth = depth or get_settings().entity_memory_depth
    merged: dict[str, list[str]] = {}
    for field_name in ENTITY_FIELDS:
        fresh = [v for v in (mentioned.get(field_name) or []) if v]
        remembered = [v for v in ((entities or {}).get(field_name) or []) if v]
        ordered: list[str] = []
        for value in [*fresh, *remembered]:
            if value not in ordered:
                ordered.append(value)
        if ordered:
            merged[field_name] = ordered[:depth]
    return merged


def most_recent(entities: dict[str, list[str]] | None, field_name: str) -> str | None:
    """The last identifier of a kind that the conversation mentioned."""
    values = (entities or {}).get(field_name) or []
    return values[0] if values else None
