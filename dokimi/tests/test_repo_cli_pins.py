# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pins for the Codex and Copilot CLI profiles and trace converters that test_repo_cli_harnesses.py leaves open. No LLM."""

from __future__ import annotations

import json

import pytest

from dokimi.repos.cli_traces import _LONG_VALUE, shell_reads
from dokimi.repos.events import convert
from dokimi.repos.harness_profile import load_profile

# The long options of GNU coreutils head, tail and nl that take a value, written out from their manuals so the
# table in cli_traces.py is checked against a list it did not generate.
_GNU_LONG_VALUE = {
    "head": {"--lines", "--bytes"},
    "tail": {"--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats"},
    "nl": {"--body-numbering", "--section-delimiter", "--footer-numbering", "--header-numbering",
           "--number-format", "--number-separator", "--number-width", "--starting-line-number",
           "--line-increment", "--join-blank-lines"},
}


def _jsonl(*records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


@pytest.mark.parametrize("tools, flags", [
    ("none", ["--sandbox", "read-only"]),
    ("read", ["--sandbox", "read-only"]),
    ("all", ["--dangerously-bypass-approvals-and-sandbox"]),
])
def test_codex_profile_maps_every_tools_mode_to_its_sandbox_flags(tools, flags):
    argv = load_profile("codex").argv(prompt="p", model="m", tools=tools, bin_path="/opt/codex")
    assert argv[argv.index("--color") + 2:-2] == flags  # between the colour option and `-- <prompt>`


def test_the_long_option_table_is_the_one_in_the_gnu_manuals():
    assert {cmd: set(opts) for cmd, opts in _LONG_VALUE.items()} == _GNU_LONG_VALUE


@pytest.mark.parametrize("command, reads", [
    ("sed --line-length 5 -n 1p a", ["a"]),         # -l N / --line-length=N: the value is not the script
    ("sed --line-length=5 -n 1p a", ["a"]),
    ("sed -l 5 -n 1p a", ["a"]),
    ("sed -n --expression 1p a", ["a"]),
    ("sed -n --file s.sed a", []),                  # the script file is not read: it may print nothing, so no read
])
def test_shell_reads_skips_the_value_of_a_sed_option(command, reads):
    assert shell_reads(command) == reads


def test_shell_reads_takes_a_command_with_carriage_returns_line_by_line():
    assert shell_reads("cat a\r\ncat b\r\n") == ["a", "b"]


@pytest.mark.parametrize("fmt, text", [
    ("codex-json", ""),
    ("copilot-json", ""),
    ("codex-json", _jsonl({"type": "item.started", "item": None}, {"type": "item.completed", "item": None})),
    ("copilot-json", _jsonl({"type": "tool.execution_start", "data": None},
                            {"type": "tool.execution_complete", "data": None})),
])
def test_an_empty_stream_or_a_record_without_a_body_records_no_read_or_command(fmt, text):
    trace = convert(fmt, text, "/ws")
    assert trace.reads() == set() and not [e for e in trace.events if e["kind"] in ("read", "command")]
    assert trace.is_error is False


def test_copilot_view_keeps_a_windows_path_as_written():
    view = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "v", "toolName": "view",
                                                          "arguments": {"path": "C:\\repo\\a.py"}}})
    assert convert("copilot-json", view, "/ws").reads() == {"C:\\repo\\a.py"}


def test_a_turn_marks_the_run_as_started_even_with_no_event():
    # main's runner takes a run with no event for one that never started, unless the trace saw a result
    codex = convert("codex-json", _jsonl({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}), "/ws")
    copilot = convert("copilot-json", _jsonl({"type": "assistant.turn_start", "data": {}}), "/ws")
    for trace in (codex, copilot):
        assert trace.events == [] and trace.result_seen is True
    assert convert("codex-json", "", "/ws").result_seen is False
    assert convert("copilot-json", "", "/ws").result_seen is False
    for raw in ('{"type":"error","message":"x"}\n', "not json at all\n", '{"type":"other"}\n'):
        assert convert("codex-json", raw, "/ws").result_seen is False, raw
        assert convert("copilot-json", raw, "/ws").result_seen is False, raw


@pytest.mark.parametrize("fmt, text", [
    ("codex-json", _jsonl({"type": "thread.started", "thread_id": "x"}) + "not json\n"),
    ("codex-json", _jsonl({"type": "error", "message": "boom"})),
    ("copilot-json", _jsonl({"type": "session.error", "data": {}}) + "not json\n"),
    ("copilot-json", _jsonl({"type": "session.start", "data": {}})),
])
def test_a_non_empty_stream_with_no_turn_leaves_result_seen_false(fmt, text):
    # only a finished turn (or a result) says the agent started; an error or an unrelated record does not
    assert convert(fmt, text, "/ws").result_seen is False
