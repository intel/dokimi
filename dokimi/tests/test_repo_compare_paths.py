# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare --pass-k` and `--path-stability`: the gate-only and trace-based measures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.compare import CompareError, compare

from test_repo_compare import _cell, _row


def _events(batch: Path, task: str, arm: str, rep: int, *commands: str) -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}"
    (rd / "events.jsonl").write_text("".join(json.dumps({"kind": "command", "text": c, "rc": 0}) + "\n"
                                             for c in commands))


def test_pass_k_says_whether_a_task_passes_every_time(tmp_path):
    b = tmp_path / "b"
    # Same gate rate (2/3) on t1 in both arms; the treatment passes t2 every time instead of 2/3.
    for arm, t1, t2 in (("none", [1, 1, 0], [1, 1, 0]), ("guidance:raw", [1, 0, 1], [1, 1, 1])):
        for rep, (x, y) in enumerate(zip(t1, t2, strict=True)):
            _cell(b, "t1", arm, rep, success=bool(x))
            _cell(b, "t2", arm, rep, success=bool(y))
    summary = compare([b], "none", pass_k=3)
    row = _row(summary, "pass^3")
    assert row["baseline_mean"] == 0.0 and row["treatment_mean"] == 0.5 and row["p_holm"] is None
    assert {t["task"]: t["delta"] for t in row["tasks"]} == {"t1": 0.0, "t2": 1.0}
    two = compare([b], "none", pass_k=2)
    assert _row(two, "pass^2")["baseline_mean"] == round(1 / 3, 6)
    short = compare([b], "none", pass_k=4)
    assert not [r for r in short["comparisons"] if r["measure"] == "pass^4"]
    assert {u["reason"] for u in short["unpaired"] if u["measure"] == "pass^4"} == {"too few cells in baseline or treatment for pass^4"}
    assert "pass^3" not in {r["measure"] for r in compare([b], "none")["comparisons"]}


def test_path_stability_compares_how_much_the_cells_paths_differ(tmp_path):
    b = tmp_path / "b"
    for rep in range(2):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep)
    _events(b, "t1", "none", 0, "grep -r x .", "pytest -q")
    _events(b, "t1", "none", 1, "git status && make check")
    _events(b, "t1", "guidance:raw", 0, "grep -r x . && pytest -q")  # one call, the same two steps
    _events(b, "t1", "guidance:raw", 1, "grep -rn y src", "pytest -q tests")
    row = _row(compare([b], "none", path_stability=True), "path_distance")
    assert row["baseline_mean"] == 1.0 and row["treatment_mean"] == 0.0 and row["delta"]["mean"] == -1.0
    assert row["tasks"][0]["n_baseline"] == 2


def test_path_stability_needs_two_traced_cells(tmp_path):
    b = tmp_path / "b"
    for rep in range(2):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep)
    _events(b, "t1", "none", 0, "pytest")
    summary = compare([b], "none", path_stability=True)
    assert not [r for r in summary["comparisons"] if r["measure"] == "path_distance"]
    assert [u["reason"] for u in summary["unpaired"] if u["measure"] == "path_distance"] == [
        "too few cells in baseline or treatment for path_distance"]


def test_pass_k_must_be_positive(tmp_path):
    _cell(tmp_path / "b", "t1", "none", 0)
    with pytest.raises(CompareError, match="at least 1"):
        compare([tmp_path / "b"], "none", pass_k=0)


def test_cli_prints_pass_k_and_path_distance(tmp_path, capsys):
    b = tmp_path / "b"
    for rep in range(2):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep, success=rep == 0)
        for arm in ("none", "guidance:raw"):
            _events(b, "t1", arm, rep, "pytest -q")
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "none", "--pass-k", "2",
                                      "--path-stability"])
    args.func(args)
    out = capsys.readouterr().out
    assert "pass^2" in out and "path_distance" in out


def _raw_events(batch: Path, task: str, arm: str, rep: int, text: str) -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}"
    (rd / "events.jsonl").write_text(text)


def test_an_empty_or_unparseable_trace_is_not_a_steady_path(tmp_path):
    b = tmp_path / "b"
    for rep in range(3):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep)
    for rep, cmd in enumerate(("grep x .", "git status", "make check")):
        _events(b, "t1", "none", rep, cmd)
    _raw_events(b, "t1", "guidance:raw", 0, "")
    _raw_events(b, "t1", "guidance:raw", 1, "not json\n[1, 2]\n5\n")
    _events(b, "t1", "guidance:raw", 2, "pytest")
    summary = compare([b], "none", path_stability=True)
    assert not [r for r in summary["comparisons"] if r["measure"] == "path_distance"]  # one traced cell


def test_a_deeply_nested_line_skips_that_line_not_the_comparison(tmp_path):
    b = tmp_path / "b"
    for rep in range(2):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep)
        _events(b, "t1", "none", rep, "pytest")
    for rep in range(2):
        _raw_events(b, "t1", "guidance:raw", rep, "[" * 200000 + "\n" + json.dumps(
            {"kind": "command", "text": "pytest", "rc": 0}) + "\n")
    row = _row(compare([b], "none", path_stability=True), "path_distance")
    assert row["treatment_mean"] == 0.0 and row["tasks"][0]["n_treatment"] == 2


def test_path_distance_is_the_mean_over_every_pair_of_three_cells(tmp_path):
    b = tmp_path / "b"
    for rep in range(3):
        _cell(b, "t1", "none", rep)
        _cell(b, "t1", "guidance:raw", rep)
        _events(b, "t1", "guidance:raw", rep, "pytest")
    _events(b, "t1", "none", 0, "pytest")
    _events(b, "t1", "none", 1, "pytest")
    _events(b, "t1", "none", 2, "git status")  # the third cell differs from both others
    row = _row(compare([b], "none", path_stability=True), "path_distance")
    assert row["baseline_mean"] == pytest.approx(2 / 3)  # pairs: 0, 1, 1
    assert row["tasks"][0]["n_baseline"] == 3


def test_pass_k_is_a_gate_measure_not_a_score_measure(tmp_path):
    b = tmp_path / "b"
    for rep in range(2):
        _cell(b, "t1", "none", rep, success=None, score=0.5)
        _cell(b, "t1", "guidance:raw", rep, success=None, score=0.7)
    summary = compare([b], "none", pass_k=2)
    assert not [r for r in summary["comparisons"] if r["measure"] == "pass^2"]
    assert "score" in {r["measure"] for r in summary["comparisons"]}


def test_the_optional_measures_do_not_flood_unpaired_with_tasks_they_do_not_apply_to(tmp_path):
    b = tmp_path / "b"
    for i in range(15):  # gate tasks with one cell per arm (the `repo run` default): too few for pass^2
        _cell(b, f"g{i}", "none", 0)
        _cell(b, f"g{i}", "guidance:raw", 0)
    for rep in range(2):  # a score task: pass^K is a gate measure
        _cell(b, "s1", "none", rep, score=1.0)
        _cell(b, "s1", "guidance:raw", rep, score=1.0)
    _cell(b, "only_base", "none", 0)
    summary = compare([b], "none", pass_k=2, path_stability=True)
    reasons = sorted((u["task"], u["reason"]) for u in summary["unpaired"])
    assert reasons == [
        ("15 task(s)", "too few cells in baseline or treatment for pass^2"),
        ("15 task(s)", "too few cells in baseline or treatment for path_distance"),
        ("only_base", "no cells in treatment"),
    ]
    assert not any(u["task"] == "s1" for u in summary["unpaired"])
    assert len(compare([b], "none")["unpaired"]) == 1
