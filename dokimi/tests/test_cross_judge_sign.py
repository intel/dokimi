# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A cross-judge measure's direction comes from its mean delta, read through the comparison verdict."""

from dokimi.eval import cross_judge as cj


def test_a_nan_score_gives_its_measure_no_direction():
    # 11 of 12 questions favour the skill, but one judged score is NaN: the mean has no sign, so no direction
    m = cj.Measure(name="p/j", kind="aggregate")
    for q in range(12):
        m.record((q, "baseline"), 1.0, None)
        m.record((q, "skill"), 3.0 if q < 11 else float("nan"), None)
    row = m.versus("skill", "baseline")
    assert row["sign"] == 0
    assert cj.label([row]) == "NO DETECTABLE EFFECT"


def test_a_nan_measure_does_not_turn_a_consistent_label_into_leaning():
    # The NaN measure is not significant, but its old sign still broke the agreement check
    clear = cj.Measure(name="p/a", kind="aggregate")
    for q in range(12):
        clear.record((q, "baseline"), 1.0, None)
        clear.record((q, "skill"), 3.0, None)
    nan = cj.Measure(name="p/b", kind="aggregate")
    for q, s in enumerate([3.0, 3.0, float("nan")]):
        nan.record((q, "baseline"), 1.0, None)
        nan.record((q, "skill"), s, None)
    rows = [clear.versus("skill", "baseline"), nan.versus("skill", "baseline")]
    assert cj.label(rows) == "CONSISTENT + (one judge)"


def _measure(name, skill_scores):
    m = cj.Measure(name=name, kind="aggregate")
    for q, s in enumerate(skill_scores):
        m.record((q, "baseline"), 1.0, None)
        m.record((q, "skill"), s, None)
    return m.versus("skill", "baseline")


def test_a_significant_nan_measure_is_not_a_second_judge():
    # p/j is significant (11 wins) but its NaN mean has no direction, so p/a alone carries the verdict
    rows = [_measure("p/a", [3.0] * 12), _measure("p/j", [3.0] * 11 + [float("nan")])]
    assert cj.label(rows) == "CONSISTENT + (one judge)"


def test_a_significant_finite_measure_with_a_zero_rounded_mean_is_still_a_second_judge():
    # Finite scores keep their label: a mean delta that rounds to 0 still counts toward the judges
    rows = [_measure("p/a", [3.0] * 12), _measure("p/j", [1.000001] * 12)]
    assert rows[1]["sign"] == 0
    assert cj.label(rows) == "CONSISTENT +"
