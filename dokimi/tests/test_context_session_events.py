# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""What a session log becomes as events: prompts, tool calls, subagent briefs, malformed lines."""
from __future__ import annotations

import json

import pytest

from dokimi.context.session_log import SessionError, parse_session, read_session


def _prompt(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _tool(name, **args):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": name, "input": args}]}}


def _session(*records, name="s"):
    return parse_session("\n".join(json.dumps(r) for r in records), name)


def _write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path



# --- session_log: what becomes an event --------------------------------------------------------

def test_prompts_are_typed_text_and_injected_records_are_not():
    session = _session(
        _prompt("CLAUDE.md echo: keep the changelog fragment current", isMeta=True),
        _prompt("<system-reminder>keep the changelog</system-reminder> please ship it"),
        _prompt("the subagent brief", isSidechain=True),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}},
    )
    assert [e.text for e in session.events] == ["please ship it"]


def test_tool_call_keeps_command_and_paths_and_drops_file_bodies():
    session = _session(_tool("Edit", file_path="/w/docs/guide.md", old_string="keep the changelog",
                             new_string="rewritten body", description="tidy"))
    (event,) = session.events
    assert event.kind == "tool"
    assert event.paths == ("/w/docs/guide.md",)
    assert "tidy" in event.text and "/w/docs/guide.md" in event.text
    assert "keep the changelog" not in event.text and "rewritten body" not in event.text


def test_bash_command_is_its_own_field_and_a_subagent_call_still_counts():
    record = _tool("Bash", command="make ci-local --offline")
    record["isSidechain"] = True
    (event,) = _session(record).events
    assert event.command == "make ci-local --offline"


def test_a_bad_line_is_reported_and_the_rest_still_reads():
    raw = "not json\n" + json.dumps(_prompt("hello there")) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["hello there"]
    assert session.errors == ["line 1: not JSON"]


def test_a_line_separator_inside_a_prompt_does_not_split_the_record():
    raw = json.dumps(_prompt("keep the changelog fragment"), ensure_ascii=False) + "\n"
    session = parse_session(raw, "x")
    assert session.errors == [] and [e.text for e in session.events] == ["keep the changelog fragment"]


def test_a_line_nested_too_deep_to_parse_is_reported_and_skipped():
    raw = "[" * 100_000 + "\n" + json.dumps(_prompt("still here")) + "\n"
    session = parse_session(raw, "x")
    assert [e.text for e in session.events] == ["still here"]
    assert session.errors == ["line 1: not JSON"]


def test_read_session_refuses_a_file_it_cannot_read_or_that_holds_no_event(tmp_path):
    with pytest.raises(SessionError, match="cannot read"):
        read_session(tmp_path / "missing.jsonl")
    empty = _write(tmp_path / "empty.jsonl", [{"type": "summary", "summary": "x"}])
    with pytest.raises(SessionError, match="no prompt and no tool call"):
        read_session(empty)


def test_a_prompt_input_of_a_tool_other_than_a_subagent_is_event_text():
    session = _session(_tool("mcp__llm__ask", prompt="review the database migration checker"))
    (event,) = session.events
    assert event.text == "review the database migration checker"


@pytest.mark.parametrize("name", ["Task", "Agent"])
def test_a_subagent_brief_in_prompt_is_not_event_text(name):
    session = _session(_tool(name, prompt="look through the migrations", description="scan"))
    (event,) = session.events
    assert "look through" not in event.text and "scan" in event.text


def test_goose_edit_bodies_are_not_event_text():
    from dokimi.context.session_log import tool_event
    event = tool_event({"path": "/w/README.md", "file_text": "Keep the changelog fragment current",
                        "old_str": "changelog", "new_str": "changelog fragment"})
    assert "changelog" not in event.text and event.paths == ("/w/README.md",)
