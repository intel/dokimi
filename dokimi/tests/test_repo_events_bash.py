# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Claude Code ``Bash`` calls that print files give ``read`` events, as Codex and Copilot shell commands do."""

from __future__ import annotations

import json

import pytest

from dokimi.repos.cli_traces import shell_reads_moved
from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics

WS = "/ws"


def _call(tool_id: str, command: str, agent: str | None = None, name: str = "Bash") -> dict:
    rec = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": {"command": command}}]}}
    return {**rec, "parent_tool_use_id": agent} if agent else rec


def _result(tool_id: str, text: str = "ok", is_error: bool = False) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": is_error, "content": text}]}}


def _events(*lines: dict, cwd: str = "") -> list[dict]:
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    return convert("claude-stream-json", "\n".join(json.dumps(x) for x in (first, *lines)), WS, cwd).events


def _reads(events: list[dict]) -> list[str]:
    return [e["path"] for e in events if e["kind"] == "read"]


def test_a_cd_then_cat_in_one_call_reads_the_file_in_that_directory():
    events = _events(_call("a", "cd benchmarks; cat AGENTS.md"), _result("a", "# rules"))
    assert events == [
        {"t": 5.0, "kind": "read", "path": "benchmarks/AGENTS.md", "rc": 0},
        {"t": 5.0, "kind": "command", "text": "cd benchmarks; cat AGENTS.md", "rc": 0, "output": "# rules",
         "output_truncated": False},
    ]


def test_an_absolute_path_inside_the_workspace_is_made_relative():
    assert _reads(_events(_call("a", "head -n 40 /ws/sub/AGENTS.md"), _result("a"))) == ["sub/AGENTS.md"]


def test_a_failed_cat_of_one_file_is_no_read():
    events = _events(_call("a", "cat AGENTS.md"), _result("a", "cat: AGENTS.md: No such file or directory", True))
    assert events == [{"t": 5.0, "kind": "command", "text": "cat AGENTS.md", "rc": 1,
                       "output": "cat: AGENTS.md: No such file or directory", "output_truncated": False}]


def test_a_failed_cat_of_two_files_keeps_both_reads():
    events = _events(_call("a", "cat AGENTS.md missing.md"), _result("a", "# rules\ncat: missing.md: No such file", True))
    assert [(e["path"], e["rc"]) for e in events if e["kind"] == "read"] == [("AGENTS.md", 0), ("missing.md", 0)]


@pytest.mark.parametrize("refusal", [
    lambda: _result("a", "PreToolUse:Bash hook error: shell background", True),
    lambda: {"type": "result", "result": "ok", "permission_denials": [{"tool_name": "Bash", "tool_use_id": "a"}]},
])
def test_a_refused_bash_call_marks_its_reads_denied(refusal):
    events = _events(_call("a", "cat AGENTS.md"), refusal())
    assert [e.get("denied") for e in events] == [True, True]
    assert process_metrics(events, lambda p: p.endswith("AGENTS.md"))["guidance_read"] == []


def _denials(*tool_ids: str) -> dict:
    return {"type": "result", "result": "ok",
            "permission_denials": [{"tool_name": "Bash", "tool_use_id": i} for i in tool_ids]}


def test_a_permission_error_then_its_denial_keeps_the_read_denied_in_place():
    events = _events(_call("a", "cat AGENTS.md"), _result("a", "Permission to use Bash has been denied", True),
                     _call("b", "cat README.md"), _result("b"), _denials("a"))
    assert [(e["kind"], e.get("path"), e.get("denied")) for e in events] == [
        ("read", "AGENTS.md", True), ("command", None, True), ("read", "README.md", None), ("command", None, None)]
    assert process_metrics(events, lambda p: p.endswith("AGENTS.md"))["tool_calls"] == 4


@pytest.mark.parametrize("refusal, reads", [
    ("hook", ["AGENTS.md"]), ("permissions", ["AGENTS.md"]), (None, []),  # None: the cd ran
])
def test_a_refused_cd_moves_no_shell(refusal, reads):
    text = {"hook": "PreToolUse:Bash hook error: no cd", "permissions": "Permission to use Bash has been denied"}
    events = _events(_call("a", "cd sub"), _result("a", text.get(refusal, ""), bool(refusal)),
                     _call("b", "cat AGENTS.md"), _result("b"), _denials("a") if refusal == "permissions" else {})
    assert _reads(events) == reads
    assert [e["kind"] for e in events] == ["command", *(["read"] if reads else []), "command"]


@pytest.mark.parametrize("command", ["cat AGENTS.md | head", "grep -n rule AGENTS.md", "cat AGENTS.md > copy.md"])
def test_a_pipe_a_search_or_a_redirect_is_no_read(command):
    assert _reads(_events(_call("a", command), _result("a"))) == []


def test_a_powershell_command_is_not_parsed():
    assert _reads(_events(_call("a", "cat AGENTS.md", name="PowerShell"), _result("a"))) == []


def test_after_a_cd_in_an_earlier_call_only_absolute_reads_are_kept():
    events = _events(
        _call("a", "cat AGENTS.md"), _result("a"),
        _call("b", "cd sub && make"), _result("b"),
        _call("c", "cat AGENTS.md"), _result("c"),  # in sub, or wherever the cd left the shell: not known
        _call("d", "cat /ws/sub/AGENTS.md"), _result("d"),
        _call("e", "cd /ws/docs && cat README.md"), _result("e"))  # a cd to an absolute path is followed
    assert _reads(events) == ["AGENTS.md", "sub/AGENTS.md", "docs/README.md"]


@pytest.mark.parametrize("first", ["(cd sub && make)", "make -C sub", "env -C sub make"])
def test_a_call_that_moves_no_shell_keeps_the_next_relative_read(first):
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md"]


@pytest.mark.parametrize("command, moves", [
    ("cd sub &", False), ("{ cd sub; } | cat", False), ("{ cd sub; } &", False), ("cd sub && make &", False),
    ("(cd sub)", False), ("cd sub", True), ("cd sub && make", True), ('cd "$X"', True), ("pushd sub", True),
    ("popd", True), ("cd sub & cd docs", True), ("{ cd sub; } & cd docs", True),
])
def test_only_a_directory_builtin_in_the_calls_own_shell_moves_it(command, moves):
    assert shell_reads_moved(command)[1] is moves


@pytest.mark.parametrize("first", ["cd sub &", "{ cd sub; } | cat", "{ cd sub; } &", "cd sub && make &"])
def test_a_cd_in_a_job_or_a_pipeline_keeps_the_next_calls_relative_read(first):
    events = _events(_call("a", first), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == ["AGENTS.md"]


def test_a_pushd_in_an_earlier_call_also_loses_the_directory():
    events = _events(_call("a", "pushd sub"), _result("a"), _call("b", "cat AGENTS.md"), _result("b"))
    assert _reads(events) == []


def test_each_agent_keeps_its_own_shell_directory():
    events = _events(
        _call("a", "cd sub"), _result("a"),
        _call("s1", "cat AGENTS.md", agent="t1"), _result("s1"),  # a subagent's shell starts at the root
        _call("p2", "cat AGENTS.md"), _result("p2"),  # the parent's shell is in sub: not recorded
        _call("s3", "cat AGENTS.md", agent="t1"), _result("s3"),  # the parent's cd did not move the subagent
        _call("s4", "cd x", agent="t1"), _result("s4"),
        _call("s5", "cat AGENTS.md", agent="t1"), _result("s5"))
    assert [(e["path"], e.get("agent")) for e in events if e["kind"] == "read"] == [
        ("AGENTS.md", "t1"), ("AGENTS.md", "t1")]


def test_a_subagent_bash_read_carries_the_agent():
    events = _events(_call("s1", "cat sub/AGENTS.md", agent="t1"), _result("s1"))
    assert [(e["kind"], e.get("agent")) for e in events] == [("read", "t1"), ("command", "t1")]


def test_the_shell_starts_in_the_directory_the_harness_started_in():
    assert _reads(_events(_call("a", "cat AGENTS.md"), _result("a"), cwd="sub")) == ["sub/AGENTS.md"]


def test_a_bash_call_with_no_result_keeps_its_read_without_an_rc():
    events = _events(_call("a", "cat AGENTS.md"))
    assert events[0] == {"t": 5.0, "kind": "read", "path": "AGENTS.md"}


def test_guidance_printed_through_bash_is_guidance_read():
    events = _events(_call("b", "cat AGENTS.md"), _result("b", "cat: AGENTS.md: No such file or directory", True),
                     _call("c", "cat README.md"), _result("c"),
                     _call("a", "cd benchmarks; cat AGENTS.md"), _result("a"))
    got = process_metrics(events, lambda p: p.endswith("AGENTS.md"))
    assert got["guidance_read"] == ["benchmarks/AGENTS.md"]
    assert got["commands"] == 3 and got["failed_commands"] == 1
