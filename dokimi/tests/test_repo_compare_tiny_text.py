# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The text tables of ``repo compare`` and the rule ledger print a tiny mean and delta at 2 significant digits (#1258)."""

from __future__ import annotations

import pytest

from dokimi.repos.compare import compare, format_compare
from dokimi.repos.compare_interval import format_delta, format_value
from dokimi.repos.ledger import format_ledger

from test_repo_compare import _cell, _row

DELTAS = [1e-12, 2e-12, 3e-12, 4e-12, 5e-12, 6e-12, 2.5e-12, 3.5e-12]


def _tiny_score_row(tmp_path):
    b = tmp_path / "b"
    for i, d in enumerate(DELTAS):
        _cell(b, f"t{i}", "guidance:none", 1, score=1e-12, score_max=None)
        _cell(b, f"t{i}", "guidance:raw", 1, score=1e-12 + d, score_max=None)
    s = compare([b], "guidance:none")
    return s, _row(s, "score")


def test_compare_text_prints_tiny_arm_means_and_delta_at_two_significant_digits(tmp_path):
    s, row = _tiny_score_row(tmp_path)
    assert (row["baseline_mean"], row["treatment_mean"], row["delta"]["mean"]) == (1e-12, 4.4e-12, 3.4e-12)
    line = next(ln for ln in format_compare(s).splitlines() if ln.lstrip().startswith("score "))
    assert line.split()[:5] == ["score", "8", "1e-12", "4.4e-12", "+3.4e-12"]
    assert "[+2e-12, +4.8e-12]" in line and "0.000" not in line


def test_ledger_text_prints_tiny_means_and_delta_at_two_significant_digits(tmp_path):
    _, row = _tiny_score_row(tmp_path)
    entry = {"rule": "R1", "arm": "guidance:raw", "baseline": "guidance:none", "model": "", "measure": "score",
             "verdict": "keep", "paired": 8, "linked": 8, "delta": row["delta"], "p": row["p"], "p_holm": row["p_holm"],
             "baseline_mean": row["baseline_mean"], "treatment_mean": row["treatment_mean"], "up": 8, "down": 0,
             "tied": 0, "reason": "r", "missing": [], "hypothesis": "h", "warnings": []}
    text = format_ledger({"ledger": "l.json", "alpha": 0.05, "family": 1, "today": "2026-11-27", "rules": [entry],
                          "warnings": []})
    assert "mean score 1e-12 -> 4.4e-12, delta +3.4e-12 [+2e-12, +4.8e-12], p " in text


def test_the_changed_line_prints_a_task_delta_two_decimals_read_as_zero_with_its_size_and_sign(tmp_path):
    b = tmp_path / "b"
    for task, base, delta in [("t0", 0.5, 1e-4), ("t1", 0.5, -1e-4), ("t2", 0.5, 2.5e-4), ("t3", 0.5, 0.25)]:
        _cell(b, task, "guidance:none", 1, score=base, score_max=None)
        _cell(b, task, "guidance:raw", 1, score=base + delta, score_max=None)
    line = next(ln for ln in format_compare(compare([b], "guidance:none")).splitlines() if "score changed:" in ln)
    assert line.split("changed: ")[1] == "t1 -0.0001, t0 +0.0001, t2 +0.00025, t3 +0.25"
    assert "+0.00 " not in line + " " and "-0.00 " not in line + " "


@pytest.mark.parametrize("value,expected", [
    (0.004, "+0.004"), (-0.004, "-0.004"), (0.005, "+0.01"), (0.25, "+0.25"), (1.0, "+1.00"), (-0.5, "-0.50"),
])
def test_format_delta_keeps_two_decimals_unless_they_read_zero(value, expected):
    assert format_delta(value) == expected


@pytest.mark.parametrize("value,signed,expected", [
    (1e-12, False, "1e-12"), (1e-12, True, "+1e-12"), (-1e-12, False, "-1e-12"), (-1e-12, True, "-1e-12"),
    (0.0, False, "0.000"), (0.0, True, "+0.000"), (None, False, "-"), (None, True, "-"),
    (0.5, False, "0.500"), (1.0, True, "+1.000"), (-0.012, True, "-0.012"), (-0.012, False, "-0.012"),
    (0.00049999, False, "0.0005"), (0.0005, False, "0.001"), (0.0005, True, "+0.001"), (-0.0004, True, "-0.0004"),
])
def test_format_value_keeps_three_decimals_unless_they_read_zero(value, signed, expected):
    assert format_value(value, signed=signed) == expected
