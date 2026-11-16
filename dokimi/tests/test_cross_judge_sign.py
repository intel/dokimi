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
