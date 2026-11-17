# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sign-flip tie width of an interaction row follows the scores of the row (issue #821).

The rule is the one of docs/benchmarking-and-comparison.md ("A delta within 1e-9 of 0 is a tie ... unless the values
of its pair are below 1"), applied to the contrast ``AB - A - B + baseline`` with the largest of the four scores as
the scale of its float noise, and to each arm delta with the larger score of its pair.
"""

from __future__ import annotations

import pytest

from dokimi.eval.interactions import interaction_test
from dokimi.eval.stats import paired_test

G = [2, 4, 3, 5, 6, 9, 7, 8]
ARMS = {"arm_a": "a", "arm_b": "b", "combined_arm": "a+b", "baseline_arm": "baseline"}


def _ev(qid: str, ab: float, a: float, b: float, base: float) -> dict:
    scores = {"a+b": ab, "a": a, "b": b, "baseline": base}
    return {"question_id": qid, "scores": {k: {"aggregate": v} for k, v in scores.items()}}


def _gains(scale: float, arm: str) -> list[dict]:
    rows = []
    for i, g in enumerate(G):
        vals = {"a+b": 0.0, "a": 0.0, "b": 0.0, "baseline": 0.0}
        vals[arm] = g * scale
        rows.append(_ev(f"q{i}", vals["a+b"], vals["a"], vals["b"], vals["baseline"]))
    return rows


@pytest.mark.parametrize("scale", [1e-3, 1e-300])
def test_a_contrast_of_tiny_scores_is_a_change_with_a_p_at_any_scale(scale):
    res = interaction_test(_gains(scale, "a+b"), **ARMS)
    assert res is not None
    assert res["n_changed"] == 8
    assert res["p"] == 0.0078
    assert res["significant"] is True


def test_the_tie_width_of_a_row_follows_its_largest_score():
    # 1e-14 on scores of 1e-3 is float noise (a tie), 1e-6 on scores of 100 is a change
    tie_row = _ev("t", 1e-3 + 1e-14, 1e-3, 1e-3, 1e-3)
    change_row = _ev("c", 100 + 1e-6, 100.0, 100.0, 100.0)
    big = [_ev(f"q{i}", g * 1e-3, 0.0, 0.0, 0.0) for i, g in enumerate(G)]
    tie = interaction_test([*big, tie_row], **ARMS)
    change = interaction_test([*big, change_row], **ARMS)
    assert tie is not None and change is not None
    assert tie["n_changed"] == 8
    assert change["n_changed"] == 9


@pytest.mark.parametrize("scale", [1e-3, 1e-300])
@pytest.mark.parametrize("arm", ["a", "b", "a+b"])
def test_an_arm_delta_of_tiny_scores_is_read_like_the_arms_table_reads_it(arm, scale):
    res = interaction_test(_gains(scale, arm), **ARMS)
    assert res is not None
    treatment = [g * scale for g in G]
    expected = paired_test(treatment, [0.0] * len(G))["delta_mean"]
    key = {"a": "delta_a", "b": "delta_b", "a+b": "delta_combined"}[arm]
    assert res[key] == expected
