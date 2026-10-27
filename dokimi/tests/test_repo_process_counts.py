# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``result.json`` ``process`` counts in `repo report` and `repo compare`, over hand-written batches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.repos.compare import compare, format_compare
from dokimi.repos.process_counts import process_values
from dokimi.repos.report import format_report, summarize
from tests.test_repo_compare import _cell, _row



def _with_process(batch: Path, process) -> None:
    """Give every cell of ``batch`` a ``process`` object (``process(cell_name)``), as #157's result.json has."""
    for path in sorted(batch.glob("*/result.json")):
        rec = json.loads(path.read_text())
        rec["process"] = process(path.parent.name)
        path.write_text(json.dumps(rec))


def test_process_counters_are_descriptive_measures(tmp_path):
    for task in ("a", "b", "c"):
        for rep in (1, 2):
            _cell(tmp_path, task, "guidance:none", rep)
            _cell(tmp_path, task, "guidance:raw", rep)

    def process(name):
        raw = "__guidance-raw__" in name
        return {"tool_calls": 10 if raw else 14, "commands": 4, "failed_commands": 0 if raw else 2,
                "denied_commands": 0, "guidance_read": ["AGENTS.md"] if raw else [],
                "counters": {"bazel": 3 if raw else 1}}

    _with_process(tmp_path, process)
    summary = compare([tmp_path], "guidance:none")
    measures = [r["measure"] for r in summary["comparisons"]]
    assert measures[:3] == ["gate", "cost_usd", "turns"]
    assert measures[3:] == ["process:tool_calls", "process:commands", "process:failed_commands",
                            "process:denied_commands", "process:guidance_read", "counter:bazel"]
    row = _row(summary, "process:tool_calls")
    assert (row["n_tasks"], row["baseline_mean"], row["treatment_mean"]) == (3, 14.0, 10.0)
    assert row["delta"]["mean"] == pytest.approx(-4.0) and row["down"] == 3 and row["p_holm"] is None
    assert _row(summary, "process:guidance_read")["delta"]["mean"] == pytest.approx(1.0)
    assert _row(summary, "counter:bazel")["delta"]["mean"] == pytest.approx(2.0)
    assert "counter:bazel" in format_compare(summary)


def test_process_absent_or_null_is_skipped(tmp_path):
    for task in ("a", "b"):
        _cell(tmp_path, task, "guidance:none", 1)
        _cell(tmp_path, task, "guidance:raw", 1)
    summary = compare([tmp_path], "guidance:none")
    assert not any(r["measure"].startswith(("process:", "counter:")) for r in summary["comparisons"])
    # a null process (the trace could not be read) and a cell before process existed are left out, not zero
    _with_process(tmp_path, lambda name: None if name.startswith("a__") else {"tool_calls": 5, "counters": None})
    summary = compare([tmp_path], "guidance:none")
    assert [r["measure"] for r in summary["comparisons"]][3:] == ["process:tool_calls"]
    assert _row(summary, "process:tool_calls")["n_tasks"] == 1
    assert {(u["task"], u["measure"]) for u in summary["unpaired"]} == {("a", "process:tool_calls")}


@pytest.mark.parametrize("arm_with_process", ["__guidance-raw__", "__guidance-none__"])
def test_process_on_one_arm_only_does_not_flood_unpaired(tmp_path, arm_with_process):
    # One arm's batch predates `process`: each shared task would otherwise be unpaired once per measure.
    for task in ("a", "b", "zz"):
        _cell(tmp_path, task, "guidance:raw", 1)
        if task != "zz":
            _cell(tmp_path, task, "guidance:none", 1)
    _with_process(tmp_path, lambda name: {"tool_calls": 3, "counters": {"bazel": 1}}
                  if arm_with_process in name else None)
    summary = compare([tmp_path], "guidance:none")
    assert not any(r["measure"].startswith(("process:", "counter:")) for r in summary["comparisons"])
    assert [(u["task"], u["measure"]) for u in summary["unpaired"]] == [("zz", None)]
    # ...but the skipped counts are named once per treatment and model, not dropped silently
    skipped = [w for w in summary["warnings"] if "only one arm reports" in w]
    assert len(skipped) == 1 and "process:tool_calls" in skipped[0] and "counter:bazel" in skipped[0]


@pytest.mark.parametrize("null_rep", [1, 2, 3])
def test_a_missing_process_value_in_a_task_is_not_zero(tmp_path, null_rep):
    # Whichever rep has no process (the first one included), the task keeps the measure from the other reps.
    for rep in (1, 2, 3):
        _cell(tmp_path, "a", "guidance:none", rep)
        _cell(tmp_path, "a", "guidance:raw", rep)
    counts = {"guidance-none": 10, "guidance-raw": 4}

    def process(name):
        if name.endswith(f"r{null_rep}"):
            return None
        return {"tool_calls": counts[name.split("__")[1]] + int(name[-1])}

    _with_process(tmp_path, process)
    row = _row(compare([tmp_path], "guidance:none"), "process:tool_calls")
    reps = [r for r in (1, 2, 3) if r != null_rep]
    assert row["baseline_mean"] == sum(10 + r for r in reps) / 2
    assert row["treatment_mean"] == sum(4 + r for r in reps) / 2


def test_process_measures_come_only_from_the_compared_arms(tmp_path):
    for arm in ("guidance:none", "guidance:raw", "guidance:other"):
        _cell(tmp_path, "a", arm, 1)
    _with_process(tmp_path, lambda name: {"tool_calls": 1, "counters": {"only_other": 1}}
                  if "__guidance-other__" in name else {"tool_calls": 1})
    summary = compare([tmp_path], "guidance:none", ["guidance:raw"])
    assert [r["measure"] for r in summary["comparisons"]][3:] == ["process:tool_calls"]


def test_report_shows_mean_process_counters(tmp_path):
    for rep in (1, 2, 3):
        _cell(tmp_path, "a", "guidance:raw", rep)
    _cell(tmp_path, "b", "guidance:raw", 1)
    counts = {"r1": 4, "r2": 8}  # r3 has a null process: left out of the mean
    _with_process(tmp_path, lambda name: None if not name.startswith("a__") or name.endswith("r3") else {
        "tool_calls": counts[name[-2:]], "commands": 2, "failed_commands": 1, "denied_commands": 0,
        "guidance_read": ["AGENTS.md", "docs/x.md"], "counters": {"bazel": 1, "bad": "x"}})
    rows = {r["task"]: r for r in summarize(tmp_path)["rows"]}
    assert rows["a"]["process"] == {"process:tool_calls": 6.0, "process:commands": 2.0,
                                    "process:failed_commands": 1.0, "process:denied_commands": 0.0,
                                    "process:guidance_read": 2.0, "counter:bazel": 1.0}
    assert rows["b"]["process"] == {}
    text = format_report(summarize(tmp_path))
    assert "process tool_calls 6, commands 2, failed_commands 1, denied_commands 0, guidance_read 2, " \
           "counter:bazel 1" in text
    assert text.count("process ") == 1


def test_process_values_skip_what_is_not_a_count():
    assert process_values({}) == {} and process_values({"process": None}) == {}
    got = process_values({"process": {"tool_calls": True, "commands": float("nan"), "failed_commands": "3",
                                      "denied_commands": 1, "guidance_read": "AGENTS.md",
                                      "counters": {"ok": 2, "bad": None}}})
    assert got == {"process:denied_commands": 1.0, "counter:ok": 2.0}
