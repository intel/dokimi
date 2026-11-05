# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context prefilter`: what one session is when Claude Code writes it to several files."""

from __future__ import annotations

import pytest

from dokimi.commands.context import _read_sessions
from dokimi.context.session_log import read_session_tree
from tests.test_context_prefilter import _prompt, _report, _row, _tool, _write

RULE = "- Run `make ci-local` when pushing a branch.\n"


def _at(second, record, **extra):
    return {**record, "timestamp": f"2026-11-01T10:00:{second:02d}.000Z", **extra}


def _subagent_corpus(root):
    """10 sessions; in the 4 that trigger, a subagent runs the rule's command, then the parent makes 12 other calls."""
    for i in range(10):
        if i >= 4:
            _write(root / f"s{i}.jsonl", [_at(1, _prompt("fix the typo in the readme"))])
            continue
        calls = [_at(20 + k, _tool("Bash", command="ls")) for k in range(12)]
        _write(root / f"s{i}.jsonl", [_at(1, _prompt("push the branch now")),
                                      _at(2, _tool("Task", description="gate", prompt="run the gate")), *calls])
        (root / f"s{i}" / "subagents").mkdir(parents=True)
        _write(root / f"s{i}" / "subagents" / "agent-1.jsonl",
               [_at(3, _prompt("run the gate"), isSidechain=True),
                _at(4, _tool("Bash", command="make ci-local"), isSidechain=True)])


@pytest.mark.parametrize("pattern", ["*.jsonl", "**/*.jsonl"])
def test_a_subagent_transcript_is_read_as_part_of_its_session_in_time_order(tmp_path, capsys, pattern):
    _subagent_corpus(tmp_path)
    sessions, failed = _read_sessions([str(tmp_path / pattern)])
    row = _row(_report(sessions, RULE, min_sessions=5, rare_max=3), "Run")
    assert (len(sessions), failed, row["status"], row["acted"], row["episodes"]) == (10, False, "in_use", 4, 4)
    assert "subagent" not in capsys.readouterr().err


def test_the_subagent_events_sit_between_the_task_call_and_the_parent_calls_after_it(tmp_path):
    _subagent_corpus(tmp_path)
    events = read_session_tree(tmp_path / "s0.jsonl").events
    assert [e.timestamp for e in events] == sorted(e.timestamp for e in events)
    assert [e.command for e in events] == ["", "", "make ci-local", *["ls"] * 12] and "gate" in events[1].text


def test_the_pushback_after_the_subagent_is_the_next_prompt_of_the_trigger(tmp_path):
    _write(tmp_path / "s.jsonl", [_at(1, _prompt("push the branch now")),
                                  _at(2, _tool("Task", description="gate", prompt="run the gate")),
                                  _at(9, _prompt("no, run make ci-local before you push"))])
    (tmp_path / "s" / "subagents").mkdir(parents=True)
    _write(tmp_path / "s" / "subagents" / "agent-1.jsonl", [_at(3, _tool("Bash", command="ls"), isSidechain=True)])
    row = _row(_report([read_session_tree(tmp_path / "s.jsonl")], RULE), "Run")
    assert (row["episodes"], row["corrected"]) == (1, 1)


@pytest.mark.parametrize(("before", "acted"), [(8, 1), (9, 0)])
def test_a_subagent_run_counts_within_the_window_of_the_parent_trigger(tmp_path, before, acted):
    _write(tmp_path / "s.jsonl", [_at(1, _prompt("push the branch now")),
                                  _at(2, _tool("Task", description="gate", prompt="run the gate")),
                                  *[_at(40 + k, _tool("Bash", command="ls")) for k in range(12)]])
    (tmp_path / "s" / "subagents").mkdir(parents=True)
    _write(tmp_path / "s" / "subagents" / "agent-1.jsonl",
           [*[_at(3 + k, _tool("Bash", command="pwd"), isSidechain=True) for k in range(before)],
            _at(30, _tool("Bash", command="make ci-local"), isSidechain=True)])
    row = _row(_report([read_session_tree(tmp_path / "s.jsonl")], RULE), "Run")
    assert row["acted"] == acted  # the run is event before + 2 after the trigger; the window is 10


def test_a_subagent_transcript_named_without_its_session_log_is_skipped_with_a_warning(tmp_path, capsys):
    _subagent_corpus(tmp_path)
    sessions, failed = _read_sessions([str(tmp_path / "s0" / "subagents" / "agent-1.jsonl")])
    assert (sessions, failed) == ([], False)
    assert "a subagent transcript without its session log; skipped" in capsys.readouterr().err


def test_a_resumed_log_that_repeats_records_is_one_session_with_each_record_once(tmp_path, capsys):
    for i in range(3):
        earlier = [_at(1, _prompt("push the branch now"), uuid=f"p{i}"),
                   _at(2, _tool("Bash", command="git push"), uuid=f"t{i}")]
        _write(tmp_path / f"a{i}.jsonl", earlier)
        _write(tmp_path / f"b{i}.jsonl", [*earlier, _at(3, _prompt("thanks"), uuid=f"n{i}")])
    for i in range(9):
        _write(tmp_path / f"c{i}.jsonl", [_at(1, _prompt("fix the typo"), uuid=f"c{i}")])
    sessions, _ = _read_sessions([str(tmp_path / "*.jsonl")])
    row = _row(_report(sessions, RULE, min_sessions=5, rare_max=3), "Run")
    assert (len(sessions), row["sessions_triggered"], row["episodes"], row["status"]) == (12, 3, 3, "rare_not_ranked")
    assert [len(s.events) for s in sessions[:3]] == [3, 3, 3]
    assert "b0.jsonl: repeats records of" in capsys.readouterr().err
