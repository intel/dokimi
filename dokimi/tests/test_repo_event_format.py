# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The version field and the schema of ``events.jsonl`` (docs/events-format.md).

The schema (``repo_event.v2.json``) pins what version 2 lines look like; the converters' output
must validate against it, an unversioned line must still read, and a reader must skip a kind it
does not know.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.artifacts import SCHEMA_DIR, schema_errors
from dokimi.repos.event_format import ACCOUNTING_KINDS, EVENT_VERSION, KNOWN_KINDS, event_version
from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics

DOCS = Path(__file__).resolve().parents[1] / "docs" / "events-format.md"


def _lines(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _claude_trace() -> str:
    usage = {"input_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100, "output_tokens": 5}

    def call(msg_id: str, *blocks: dict, stamp: str) -> dict:
        return {"type": "assistant", "timestamp": stamp,
                "message": {"id": msg_id, "model": "m", "content": list(blocks), "usage": usage}}

    def result(tool_id: str, text: str, stamp: str) -> dict:
        return {"type": "user", "timestamp": stamp,
                "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}

    return _lines(
        {"type": "system", "subtype": "init", "model": "m", "claude_code_version": "2.0.0", "tools": []},
        call("a", {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cat AGENTS.md"}},
             stamp="2026-11-14T10:00:00Z"),
        result("b1", "rules", "2026-11-14T10:00:01Z"),
        call("b", {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "src/a.py"}},
             {"type": "tool_use", "id": "w1", "name": "WebFetch", "input": {"url": "x"}},
             stamp="2026-11-14T10:00:02Z"),
        result("e1", "ok", "2026-11-14T10:00:03Z"),
        result("w1", "denied", "2026-11-14T10:00:03Z"),
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-14T10:00:04Z"},
        {"type": "result", "result": "done", "num_turns": 2, "is_error": False, "subtype": "success",
         "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 100}},
    )


def _codex_trace() -> str:
    return _lines(
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "cat src/a.py"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                            "command": "cat src/a.py", "aggregated_output": "x", "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}},
    )


def _converted(raw_format: str, raw: str) -> list[dict]:
    """The events of a converted trace as the writer puts them on the lines of ``events.jsonl``."""
    return [json.loads(line) for line in convert(raw_format, raw).events_jsonl().splitlines()]


def test_the_schema_accepts_the_committed_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "artifacts" / "repo_event.v1.json").read_text())
    assert schema_errors("repo_event.v1", fixture) == []


def test_every_converted_line_is_versioned_and_validates():
    lines = [*_converted("claude-stream-json", _claude_trace()), *_converted("codex-json", _codex_trace())]
    assert {e["kind"] for e in lines} == KNOWN_KINDS | ACCOUNTING_KINDS  # every kind a converter writes
    for event in lines:
        assert event["v"] == EVENT_VERSION
        assert schema_errors("repo_event.v2", event) == [], event


@pytest.mark.parametrize("line", [
    {"kind": "command", "text": "ls", "t": 1.0, "v": 2},
    {"kind": "command", "rc": 0, "t": 1.0, "v": 2},
    {"kind": "edit", "t": 1.0, "v": 2},
    {"kind": "tool", "t": 1.0, "v": 2},
    {"kind": "read", "path": 3, "t": 1.0, "v": 2},
    {"kind": "command", "text": "ls", "rc": 0, "t": "1.0", "v": 2},
    {"kind": "read", "path": "a.md", "v": 2},
    {"t": 1.0, "v": 2},
], ids=["command-no-rc", "command-no-text", "edit-no-path", "tool-no-name", "read-path-int", "string-t",
        "no-t", "no-kind"])
def test_a_line_that_breaks_the_version_2_shape_is_refused(line):
    assert schema_errors("repo_event.v2", line) != []


def test_the_writer_only_adds_the_version_to_each_event():
    transcript = convert("claude-stream-json", _claude_trace())
    written = [json.loads(line) for line in transcript.events_jsonl().splitlines()]
    assert [{k: v for k, v in e.items() if k != "v"} for e in written] == transcript.events


def test_an_unversioned_line_is_accepted_and_reads_as_version_1():
    legacy = {"t": 1.0, "kind": "command", "text": "ls", "rc": 0}
    assert schema_errors("repo_event.v1", legacy) == []
    assert event_version(legacy) == 1


def test_a_line_of_a_later_major_version_is_not_a_version_1_line():
    later = {"t": 1.0, "kind": "command", "text": "ls", "rc": 0, "v": 2}
    assert schema_errors("repo_event.v1", later) != []
    assert event_version(later) == 2


def test_a_reader_skips_a_kind_it_does_not_know():
    future = {"t": 1.0, "kind": "no-such-kind", "name": "p", "v": 1}
    assert schema_errors("repo_event.v1", future) == []  # the schema admits any kind
    base = [{"t": 1.0, "kind": "command", "text": "ls", "rc": 0}]
    assert process_metrics([*base, future], lambda _: False)["tool_calls"] == process_metrics(base, lambda _: False)["tool_calls"]
    assert process_metrics([*base, future], lambda _: False)["commands"] == 1
    tool = {"t": 2.0, "kind": "tool", "name": "Skill", "skill": "lint-fix"}
    assert process_metrics([tool], lambda _: False)["tool_calls"] == 1


def test_unversioned_and_converted_give_the_same_process_metrics():
    versioned = _converted("claude-stream-json", _claude_trace())
    unversioned = [{k: v for k, v in e.items() if k != "v"} for e in versioned]
    assert process_metrics(versioned, lambda p: p == "AGENTS.md") == process_metrics(unversioned, lambda p: p == "AGENTS.md")


def test_the_schema_names_exactly_the_known_kinds():
    schema = json.loads((SCHEMA_DIR / "repo_event.v2.json").read_text(encoding="utf-8"))
    named = {clause["if"]["properties"]["kind"]["const"] for clause in schema["allOf"]}
    assert named == KNOWN_KINDS


def test_the_doc_names_every_known_kind_and_the_version_field():
    text = DOCS.read_text(encoding="utf-8")
    for kind in sorted(KNOWN_KINDS | {"probe", "choice"}):  # the runner adds probe events after conversion
        assert f"| `{kind}` |" in text, kind
    assert "`v`" in text and "repo_event.v2.json" in text


def test_the_doc_examples_validate_against_the_version_2_schema():
    block = DOCS.read_text(encoding="utf-8").split("```json\n", 1)[1].split("```", 1)[0]
    lines = [json.loads(line) for line in block.splitlines() if line.strip()]
    assert len(lines) >= 7
    for line in lines:
        assert schema_errors("repo_event.v2", line) == [], line
