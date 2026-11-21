# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The probe events of `repo run`: a failing matcher and the patterns a cell records (contract §2, §4.1)."""

from __future__ import annotations

import json

from dokimi.repos import security as sec
from test_repo_run import _run, setup  # noqa: F401  # the run fixture, with its helpers


def test_a_probe_matcher_that_raises_keeps_the_cell_graded_with_its_events(setup, monkeypatch):  # noqa: F811
    # The matcher runs after the agent: a failure costs the probe events, not the grade, answer or events.jsonl.
    def refuse(*args, **kwargs):
        raise RecursionError("a chain too deep")

    monkeypatch.setattr("dokimi.repos.runner.with_probes", refuse)
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    cell = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    rec = json.loads((cell / "result.json").read_text())
    assert rec["grade"]["status"] == "graded" and rec["grade"]["success"] is True
    assert (cell / "events.jsonl").exists() and (cell / "answer.txt").exists()
    assert any("probe matching failed" in e and "a chain too deep" in e for e in rec["agent"]["trace_errors"])
    # the note drops no trace line, so the security scan still measures the cell instead of leaving it unfinished
    cov = sec.scan_batch(setup["out"] / "b1", "guidance:raw")["coverage"]
    assert cov["cells"] == 1 and cov["unfinished"] == []


def test_the_cell_records_the_probe_patterns_it_ran_with(setup):  # noqa: F811
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    meta = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/meta.json").read_text())
    assert "nproc" in meta["probes"]["cpu"] and meta["probes"]["packages"] == ["pip list", "pip show", "pip freeze", "conda list"]

def test_commands_the_matcher_cannot_read_cost_only_their_own_probes(setup, monkeypatch):  # noqa: F811
    deep = "echo " + "$(" * 1500 + "x" + ")" * 1500  # too deep for the matcher: it raises RecursionError
    calls = [["Bash", {"command": deep}] for _ in range(5)] + [["Bash", {"command": "nproc"}]]
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps(calls))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    cell = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    events = [json.loads(line) for line in (cell / "events.jsonl").read_text().splitlines()]
    assert [(e["kind"], e.get("text")) for e in events if e["kind"] == "probe"] == [("probe", "nproc")]
    notes = json.loads((cell / "result.json").read_text())["agent"]["trace_errors"]
    assert [n for n in notes if n.startswith("probe matching failed (RecursionError)")] == notes and len(notes) == 1
    # one note for the cell: five would fill the runner's five trace_errors slots and the scan would call it unfinished
    cov = sec.scan_batch(setup["out"] / "b1", "guidance:raw")["coverage"]
    assert cov["cells"] == 1 and cov["unfinished"] == []


def test_choice_matching_failure_keeps_the_grade_and_trace(setup, monkeypatch):  # noqa: F811
    def failed(*args):
        raise ValueError("no match")

    monkeypatch.setattr("dokimi.repos.runner.with_choices", failed)
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    cell = setup["out"] / "b1/fix_add__guidance-raw__m-1__r1"
    result = json.loads((cell / "result.json").read_text())
    assert result["grade"]["success"] is True
    assert (cell / "events.jsonl").read_text() and (cell / "answer.txt").read_text()
    assert any("choice matching failed" in reason for reason in result["agent"]["trace_errors"])
