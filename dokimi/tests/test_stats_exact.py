# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The unrounded statistics a paired test keeps next to its display fields."""

from __future__ import annotations

import pytest

from dokimi.eval import stats


def test_exact_holds_the_unrounded_delta_and_interval():
    res = stats.paired_test([1.0, 2.0, 4.0, 8.0], [0.0, 0.0, 0.0, 1.0 / 3])
    deltas = [1.0, 2.0, 4.0, 8.0 - 1.0 / 3]

    assert res["exact"]["delta_mean"] == pytest.approx(sum(deltas) / 4, abs=1e-12)
    assert res["delta_mean"] == round(res["exact"]["delta_mean"], 2)
    assert res["ci_lo"] == round(res["exact"]["ci_lo"], 4)
    assert res["ci_hi"] == round(res["exact"]["ci_hi"], 4)
    assert res["exact"]["ci_lo"] != res["ci_lo"]  # the rounding is real on this input


def test_exact_p_adjusted_is_the_family_correction_before_rounding():
    # Seven pairs per comparison: below MIN_PAIRS_FOR_TEST (6) there is no p to correct,
    # and the assertions would pass on None == None.
    results = [
        stats.paired_test([1.0, 2.5, 3.1, 4.7, 5.2, 6.0, 7.1], [0.0, 0.1, 0.0, 0.2, 0.3, 0.1, 0.0], rounded=False),
        stats.paired_test([0.3, -0.2, 0.9, 0.1, 0.5, 0.4, 0.8], [0.0] * 7, rounded=False),
        stats.paired_test([2.0, 1.5, 2.2, 1.9, 2.4, 1.1, 2.8], [1.0, 0.9, 1.2, 1.0, 1.1, 0.8, 1.5], rounded=False),
    ]
    assert all(r["p_ttest"] is not None for r in results)
    stats._apply_correction(results, correction="holm", label="arms")
    exact = stats.adjust_pvalues([r["p_ttest"] for r in results], method="holm", rounded=False)

    for res, p in zip(results, exact, strict=True):
        assert res["exact"]["p_adjusted"] is not None
        assert res["exact"]["p_adjusted"] == p
        assert res["p_adjusted"] == stats._round_p(p)
    # The first comparison's exact corrected p is not its displayed value: the rounding is real here.
    assert results[0]["exact"]["p_adjusted"] != results[0]["p_adjusted"]
