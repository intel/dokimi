# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``searched``: the guidance files a ``grep``, ``rg``, ``git grep`` or Claude Code ``Grep`` call showed lines of
(contract §4.1, ``guidance_searched``), from the three harness traces to ``process``."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import schema_errors
from dokimi.repos.cli_traces import shell_walked
from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics
from test_repo_events_bash import WS, _call, _denials, _events, _result


def _process(*lines: dict, cwd: str = "") -> dict:
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    t = convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), WS, cwd)
    return process_metrics(t.events, lambda p: p.endswith("AGENTS.md"), unknown_reads=t.unknown_reads)


def _grep_tool(tool_id: str, **given: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Grep", "input": {"pattern": "warn", **given}}]}}


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


@pytest.mark.parametrize("command, searched", [
    ('grep -n -i "deprecat\\|warn" dpnp/AGENTS.md dpnp/tests/AGENTS.md', ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"]),
    ("rg -n warn dpnp/tests/AGENTS.md", ["dpnp/tests/AGENTS.md"]),
    ("git grep -n warn -- dpnp/AGENTS.md", ["dpnp/AGENTS.md"]),
    ("git grep -e warn -- dpnp/AGENTS.md", ["dpnp/AGENTS.md"]),
    ("egrep warn AGENTS.md", ["AGENTS.md"]),
    ("fgrep warn AGENTS.md", ["AGENTS.md"]),
    ("/bin/grep warn AGENTS.md", ["AGENTS.md"]),
    ("command grep warn AGENTS.md", ["AGENTS.md"]),
    ("grep --line-number --color=always warn AGENTS.md", ["AGENTS.md"]),  # --line-number is not -l, --color not -c
    ("time -p grep warn AGENTS.md | head", ["AGENTS.md"]),  # time is bash's keyword: it times the pipeline
    ("time grep warn AGENTS.md", ["AGENTS.md"]),
    ("rg -L warn AGENTS.md", ["AGENTS.md"]),  # rg's -L follows links; it does not list files without a match
    ("grep warn AGENTS.md -", ["AGENTS.md"]),  # - is stdin, no file
    ("grep -m 1 warn AGENTS.md", ["AGENTS.md"]),  # 1 is the value of -m, not a file
    ("grep -nA2 warn AGENTS.md", ["AGENTS.md"]),
    ("grep -e warn -f patterns.txt AGENTS.md", ["AGENTS.md"]),  # the pattern file is read, not searched
    ("grep warn AGENTS.md 2>/dev/null", ["AGENTS.md"]),  # stderr only
    ("cd sub && grep warn AGENTS.md", ["sub/AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20", ["AGENTS.md"]),
    ("grep 'a|b' AGENTS.md | head -n 20", ["AGENTS.md"]),  # a pipe in quotes is no pipe
    ("rg warn AGENTS.md | tail -5", ["AGENTS.md"]),
    ("grep warn AGENTS.md | head -n 20 | tail --lines=5", ["AGENTS.md"]),
    ("grep warn AGENTS.md 2>&1 | head", ["AGENTS.md"]),
    ("if grep warn AGENTS.md; then echo y; fi", ["AGENTS.md"]),
])
def test_a_search_names_the_files_it_shows_lines_of(command, searched):
    walked = shell_walked(command)
    assert walked.searched == searched
    assert walked.reads == []  # a search is no read: guidance_read keeps its meaning


@pytest.mark.parametrize("command", [
    "grep -q warn AGENTS.md", "grep --quiet warn AGENTS.md", "grep --silent warn AGENTS.md",
    "grep -l warn AGENTS.md", "grep --files-with-matches warn AGENTS.md", "rg -l warn AGENTS.md",
    "grep -L warn AGENTS.md", "grep --files-without-match warn AGENTS.md", "git grep -L warn -- AGENTS.md",
    "grep -c warn AGENTS.md", "grep -nc warn AGENTS.md", "grep --count warn AGENTS.md",
    "rg -c warn AGENTS.md", "rg --count-matches warn AGENTS.md", "rg --files AGENTS.md sub/AGENTS.md", "git grep -l warn -- AGENTS.md",
    "grep -r warn docs", "grep -R warn .", "rg warn docs",  # a directory names no guidance file
    "grep warn *.md", "grep warn $FILE", "grep warn $(ls AGENTS.md)", "grep warn AGENTS.{md,txt}",  # computed operands
    "grep warn AGENTS.md > hits.txt", "grep warn AGENTS.md >/dev/null", "grep warn AGENTS.md &>/dev/null",
    "grep warn AGENTS.md > /dev/null 2>&1",
    "! grep warn AGENTS.md", "grep warn - < AGENTS.md", "cat AGENTS.md | grep warn", "grep -f AGENTS.md warn",
    "grep warn AGENTS.md | wc -l", "grep warn AGENTS.md | sort", "grep warn AGENTS.md | head -n 5 other.md",
    "grep warn AGENTS.md | head > out.txt", "(grep warn AGENTS.md)", "git -C sub grep warn -- AGENTS.md",
    "grep() { :; }; grep warn AGENTS.md", "grep() { :; }; grep warn AGENTS.md | head", "grep --help AGENTS.md AGENTS.md",
    "cd -; grep warn AGENTS.md",  # the directory is not known
    "{ grep warn AGENTS.md; } > /dev/null",
])
def test_a_search_that_shows_no_line_of_a_file_is_not_searched(command):
    got = _process(_call("a", command), _result("a"))
    assert got["guidance_searched"] == [] and got["guidance_read"] == []
    assert got["unknown_reads"] == 0  # unknown_reads counts reads, a search never adds to it


def test_the_issues_grep_over_two_nested_files_is_searched_and_not_read():
    command = 'grep -n -i "deprecat\\|warn" dpnp/AGENTS.md dpnp/tests/AGENTS.md'
    events = _events(_call("a", command), _result("a", "dpnp/AGENTS.md:3:warn"))
    assert events == [{"t": 5.0, "kind": "command", "text": command, "rc": 0, "output": "dpnp/AGENTS.md:3:warn",
                       "output_truncated": False, "searched": ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"]}]
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert (got["guidance_read"], got["guidance_searched"]) == ([], ["dpnp/AGENTS.md", "dpnp/tests/AGENTS.md"])
    assert got["tool_calls"] == 1  # the search adds no event: tool_calls counts the same


def test_a_search_of_one_file_that_failed_shows_no_line():
    # grep exits 1 when no line matched and 2 on an error; Claude Code reports both as is_error
    assert _process(_call("a", "grep warn AGENTS.md"), _result("a", "", True))["guidance_searched"] == []
    assert _process(_call("a", "cd sub && grep warn AGENTS.md"), _result("a", "", True))["guidance_searched"] == []
    assert _process(_call("a", "grep warn AGENTS.md"), _result("a", "AGENTS.md:1:warn"))["guidance_searched"] == ["AGENTS.md"]


def test_a_search_of_several_files_keeps_them_all_as_a_multi_file_read_does():
    command = "grep warn AGENTS.md sub/AGENTS.md"
    assert _process(_call("a", command), _result("a"))["guidance_searched"] == ["AGENTS.md", "sub/AGENTS.md"]
    assert _process(_call("a", command), _result("a", "x", True))["guidance_searched"] == ["AGENTS.md", "sub/AGENTS.md"]


def test_a_refused_search_searched_nothing():
    got = _process(_call("a", "grep warn AGENTS.md"), _denials("a"))
    assert got["guidance_searched"] == [] and got["denied_commands"] == 1


def test_a_relative_search_after_an_earlier_calls_cd_is_not_counted_and_is_not_an_unknown_read():
    lines = (_call("a", "cd sub"), _result("a"), _call("b", "grep warn AGENTS.md /ws/docs/AGENTS.md"), _result("b"))
    got = _process(*lines)
    assert (got["guidance_searched"], got["unknown_reads"]) == (["docs/AGENTS.md"], 0)  # the absolute one stays
    # a read in the same position is counted unknown, as before
    assert _process(_call("a", "cd sub"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))["unknown_reads"] == 1


def test_the_search_starts_in_the_directory_the_harness_started_in():
    got = _process(_call("a", "grep warn AGENTS.md"), _result("a"), cwd="sub")
    assert got["guidance_searched"] == ["sub/AGENTS.md"]


def test_a_grep_tool_call_in_content_mode_over_a_file_is_searched():
    events = _events(_grep_tool("g", path=f"{WS}/dpnp/AGENTS.md", output_mode="content"), _result("g", "dpnp/AGENTS.md:1:x"))
    assert [(e["kind"], e["name"], e["searched"]) for e in events] == [("tool", "Grep", ["dpnp/AGENTS.md"])]
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert (got["guidance_searched"], got["guidance_read"], got["tool_calls"]) == (["dpnp/AGENTS.md"], [], 1)


@pytest.mark.parametrize("given", [
    {"path": f"{WS}/AGENTS.md"},  # Claude Code's default mode lists the names of files and shows no line
    {"path": f"{WS}/AGENTS.md", "output_mode": "files_with_matches"},
    {"path": f"{WS}/AGENTS.md", "output_mode": "count"},
    {"output_mode": "content"},  # no path: it searches the working directory
    {"path": f"{WS}/docs", "output_mode": "content"},  # a directory is no guidance file
])
def test_a_grep_tool_call_that_shows_no_line_of_a_guidance_file_is_not_searched(given):
    got = _process(_grep_tool("g", **given), _result("g"))
    assert got["guidance_searched"] == []
    if given.get("output_mode") != "content" or "path" not in given:  # it names no file: the event has no list
        assert all("searched" not in e for e in _events(_grep_tool("g", **given), _result("g")))


def test_a_failed_or_refused_grep_tool_call_searched_nothing():
    given = {"path": f"{WS}/AGENTS.md", "output_mode": "content"}
    assert _process(_grep_tool("g", **given), _result("g", "error", True))["guidance_searched"] == []
    refused = {"type": "result", "result": "ok", "permission_denials": [{"tool_name": "Grep", "tool_use_id": "g"}]}
    assert _process(_grep_tool("g", **given), refused)["guidance_searched"] == []


def _codex(command: str, rc: int | None, started: bool) -> str:
    item = {"id": "a", "type": "command_execution", "command": command}
    done = {"type": "item.completed", "item": {**item, "exit_code": rc}}
    return _jsonl(*([{"type": "item.started", "item": item}] if started else []), done)


@pytest.mark.parametrize("started", [True, False], ids=["started", "completed only"])
def test_codex_searches_follow_the_same_rules(started):
    def searched(command, rc):
        t = convert("codex-json", _codex(command, rc, started), WS)
        return [e.get("searched") for e in t.events if e["kind"] == "command"]

    assert searched("/usr/bin/bash -lc 'rg -n warn dpnp/AGENTS.md'", 0) == [["dpnp/AGENTS.md"]]
    assert searched("rg -n warn dpnp/AGENTS.md", 1) == [None]  # one file, failed
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 1) == [["AGENTS.md", "sub/AGENTS.md"]]
    assert searched("grep -q warn AGENTS.md", 0) == [None]


def test_copilot_bash_searches_follow_the_same_rules():
    def searched(command, rc):
        raw = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "bash",
                                                                "arguments": {"command": command}}},
                     {"type": "tool.execution_complete", "data": {"toolCallId": "a", "success": rc == 0,
                                                                  "shellExecution": {"exitCode": rc}}})
        return [e.get("searched") for e in convert("copilot-json", raw, WS).events if e["kind"] == "command"]

    assert searched("grep -n warn AGENTS.md", 0) == [["AGENTS.md"]]
    assert searched("grep -n warn AGENTS.md", 1) == [None]
    assert searched("grep warn AGENTS.md sub/AGENTS.md", 1) == [["AGENTS.md", "sub/AGENTS.md"]]
    powershell = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "a", "toolName": "powershell",
                                                                   "arguments": {"command": "grep warn AGENTS.md"}}})
    assert "searched" not in convert("copilot-json", powershell, WS).events[0]  # PowerShell is not parsed


def test_the_schema_takes_a_list_of_paths_in_searched():
    event = {"v": 1, "kind": "command", "t": None, "text": "grep x AGENTS.md", "rc": 0}
    assert schema_errors("repo_event.v1", {**event, "searched": ["AGENTS.md"]}) == []
    assert schema_errors("repo_event.v1", {**event, "searched": "AGENTS.md"}) != []
    assert schema_errors("repo_event.v1", {**event, "searched": [1]}) != []
