# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Goose session stores read by `context prefilter`: synthetic SQLite stores under ``tmp_path``.

The table and column names follow the block/goose source (session storage and message
serialization); no real store is read.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from dokimi.cli import build_parser
from dokimi.context.goose_log import is_goose_store, read_goose_store
from dokimi.context.session_log import SessionError

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_timestamp INTEGER NOT NULL,
    message_id TEXT,
    metadata_json TEXT
);
"""
GUIDE = "- Keep the changelog fragment current.\n"


def _text(words):
    return [{"type": "text", "text": words}]


def _call(command=None, path=None):
    args = {}
    if command is not None:
        args["command"] = command
    if path is not None:
        args["path"] = path
    return [{"type": "toolRequest", "id": "c1",
             "toolCall": {"status": "success", "value": {"name": "developer__shell", "arguments": args}}}]


def _result():
    return [{"type": "toolResponse", "id": "c1", "toolResult": {"status": "success", "value": {}}}]


def _store(tmp_path, sessions, name="sessions.db"):
    """``sessions`` maps a session id to its rows: (role, content, metadata or None), in order."""
    db = tmp_path / name
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for n, (sid, rows) in enumerate(sessions.items()):
        conn.execute("INSERT INTO sessions VALUES (?, ?)", (sid, f"2026-10-30T10:0{n}:00Z"))
        for ts, (role, content, meta) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO messages (session_id, role, content_json, created_timestamp, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, role, json.dumps(content), ts, json.dumps(meta) if meta is not None else None))
    conn.commit()
    conn.close()
    return db


def test_each_row_is_a_session_and_a_session_without_a_prompt_or_call_is_dropped(tmp_path):
    db = _store(tmp_path, {
        "s0": [("user", _result(), None)],
        "s1": [("user", _text("keep the changelog fragment current"), None),
               ("assistant", _call(command="make ci-local"), None),
               ("user", _result(), None)],
    })
    (session,) = read_goose_store(db)
    assert session.name == f"{db}#s1"
    assert [(e.kind, e.text) for e in session.events] == [
        ("prompt", "keep the changelog fragment current"), ("tool", "make ci-local")]
    assert session.events[1].command == "make ci-local"


def test_a_session_of_unreadable_messages_only_is_not_counted(tmp_path):
    db = _store(tmp_path, {"s0": [("user", _text("push the branch now"), None)], "s1": []})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (session_id, role, content_json, created_timestamp) "
                 "VALUES ('s1', 'user', 'not json', 1)")
    conn.commit()
    conn.close()
    sessions = read_goose_store(db)
    assert [s.name for s in sessions] == [f"{db}#s0"]


def test_tool_arguments_come_from_the_tool_call_value_and_name_a_path(tmp_path):
    db = _store(tmp_path, {"s1": [("assistant", _call(path="/w/docs/guide.md"), None)]})
    (session,) = read_goose_store(db)
    assert session.events[0].paths == ("/w/docs/guide.md",)


def test_a_message_the_user_cannot_see_is_not_a_prompt(tmp_path):
    db = _store(tmp_path, {"s1": [
        ("user", _text("note for the agent only"), {"userVisible": False, "agentVisible": True}),
        ("user", _text("the user's own words"), {"userVisible": True, "agentVisible": True}),
        ("user", _text("a prompt without metadata"), None),
    ]})
    (session,) = read_goose_store(db)
    assert [e.text for e in session.events] == ["the user's own words", "a prompt without metadata"]


def test_a_message_with_bad_content_is_reported_and_the_rest_still_read(tmp_path):
    db = _store(tmp_path, {"s1": [("user", _text("push the branch now"), None)]})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (session_id, role, content_json, created_timestamp) "
                 "VALUES ('s1', 'user', 'not json', 9)")
    conn.commit()
    conn.close()
    (session,) = read_goose_store(db)
    assert [e.text for e in session.events] == ["push the branch now"]
    assert len(session.errors) == 1 and session.errors[0].endswith("content is not JSON")


def test_a_missing_store_is_refused_and_nothing_is_created(tmp_path):
    missing = tmp_path / "none.db"
    with pytest.raises(SessionError, match="cannot read"):
        read_goose_store(missing)
    assert not missing.exists()


def test_a_file_that_is_not_a_store_is_refused(tmp_path):
    plain = tmp_path / "plain.db"
    plain.write_text("hello, not sqlite\n", encoding="utf-8")
    assert not is_goose_store(plain)
    with pytest.raises(SessionError, match="not a Goose sessions store"):
        read_goose_store(plain)


def test_a_store_with_no_prompt_or_tool_call_is_refused(tmp_path):
    db = _store(tmp_path, {"s1": [("user", _result(), None)]})
    with pytest.raises(SessionError, match="no prompt and no tool call"):
        read_goose_store(db)


def test_the_command_reads_a_store_and_a_claude_log_together(tmp_path, capsys):
    guide = tmp_path / "AGENTS.md"
    guide.write_text(GUIDE, encoding="utf-8")
    db = _store(tmp_path, {"s1": [("user", _text("fix the readme typo"), None)]})
    claude = tmp_path / "claude.jsonl"
    claude.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "fix the readme"}}) + "\n",
                      encoding="utf-8")
    args = build_parser().parse_args(["context", "prefilter", str(guide), str(db), str(claude),
                                      "--min-sessions", "1", "--json"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["sessions"] == 2


def test_a_user_message_of_several_text_blocks_is_one_prompt(tmp_path):
    db = _store(tmp_path, {"s1": [("user", [{"type": "text", "text": "update the changelog fragment"},
                                            {"type": "text", "text": "no, update the changelog fragment first"}],
                                   None)]})
    (session,) = read_goose_store(db)
    assert [e.kind for e in session.events] == ["prompt"]


def test_an_unreadable_only_session_is_dropped_from_the_corpus_and_its_diagnostic_is_kept(tmp_path):
    from dokimi.commands.context import _read_sessions
    db = _store(tmp_path, {"s0": [("user", _text("push the branch now"), None)], "s1": []})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (session_id, role, content_json, created_timestamp) "
                 "VALUES ('s1', 'user', 'not json', 1)")
    conn.commit()
    conn.close()
    dropped: list = []
    sessions = read_goose_store(db, dropped)
    assert [s.name for s in sessions] == [f"{db}#s0"]
    assert len(dropped) == 1 and dropped[0].errors[0].endswith("content is not JSON")
    loaded, failed = _read_sessions([str(db)])
    assert len(loaded) == 1 and not failed


def test_a_goose_session_with_a_bad_message_reports_it_on_the_command_line(tmp_path, capsys):
    from dokimi.commands.context import _read_sessions
    db = _store(tmp_path, {"s0": [("user", _text("push the branch now"), None)], "s1": []})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO messages (session_id, role, content_json, created_timestamp) "
                 "VALUES ('s1', 'user', 'not json', 1)")
    conn.commit()
    conn.close()
    _read_sessions([str(db)])
    assert "content is not JSON" in capsys.readouterr().err
