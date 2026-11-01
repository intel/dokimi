# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Goose session store (``~/.local/share/goose/sessions/sessions.db``) -> one session per row.

Goose keeps messages in SQLite: ``messages.session_id`` names the session and ``content_json``
is a list of blocks. A ``text`` block of a ``user`` message is a prompt, unless the message's
``metadata_json`` marks it not user-visible (a note injected for the agent). A ``toolRequest``
block of an ``assistant`` message is a tool call, its arguments under ``toolCall.value``.
Results (``toolResponse``) are not events. The layout comes from the block/goose source (session
storage and message serialization). The store is opened read-only; nothing is written.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from dokimi.context.session_log import Event, Session, SessionError, add_error, prompt_text, tool_event

SQLITE_MAGIC = b"SQLite format 3\x00"


def is_goose_store(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def read_goose_store(path: Path, dropped: list[Session] | None = None) -> list[Session]:
    """The sessions of a Goose store that hold a prompt or a tool call, oldest first.

    A session with no prompt or call is not counted; given ``dropped``, it is appended there so its
    diagnostics can still be reported.
    """
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise SessionError(f"cannot read {path}: {exc}") from exc
    try:
        ids = [sid for (sid,) in conn.execute("SELECT id FROM sessions ORDER BY created_at, id")]
        sessions = [_session_of(conn, sid, f"{path}#{sid}") for sid in ids]
    except sqlite3.Error as exc:
        raise SessionError(f"{path}: not a Goose sessions store: {exc}") from exc
    finally:
        conn.close()
    kept = [s for s in sessions if s.events]  # a session of unreadable messages only is no session
    if dropped is not None:
        dropped.extend(s for s in sessions if not s.events and s.errors)
    if not kept:
        raise SessionError(f"{path}: no prompt and no tool call in any session")
    return kept


def _session_of(conn: sqlite3.Connection, session_id: str, name: str) -> Session:
    session = Session(name=name)
    rows = conn.execute(
        "SELECT id, role, content_json, metadata_json FROM messages WHERE session_id = ? "
        "ORDER BY created_timestamp, id", (session_id,)).fetchall()
    for row_id, role, content_json, metadata_json in rows:
        try:
            content = json.loads(content_json or "[]")
        except (ValueError, RecursionError):  # not JSON, or nested too deeply
            add_error(session, f"message {row_id}: content is not JSON")
            continue
        session.events.extend(_events_of(role, content, _user_visible(metadata_json)))
    return session


def _user_visible(metadata_json: str | None) -> bool:
    """A message without the mark counts as the user's; only ``userVisible: false`` excludes it."""
    try:
        meta = json.loads(metadata_json) if metadata_json else {}
    except (ValueError, RecursionError):
        return True
    return not (isinstance(meta, dict) and meta.get("userVisible") is False)


def _events_of(role: str, content: Any, user_visible: bool) -> list[Event]:
    if not isinstance(content, list):
        return []
    events = []
    if role == "user" and user_visible:  # the text blocks of one message are one prompt
        texts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        words = prompt_text(" ".join(t for t in texts if isinstance(t, str)))
        if words:
            events.append(Event("prompt", words))
    for block in content:
        if isinstance(block, dict) and role == "assistant" and block.get("type") == "toolRequest":
            call = block.get("toolCall")
            value = call.get("value") if isinstance(call, dict) else None
            if isinstance(value, dict):
                events.append(tool_event(value.get("arguments")))
    return events
