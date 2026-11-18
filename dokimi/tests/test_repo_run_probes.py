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
