# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Monte Carlo ends of a row whose deltas span more than a float sum resolves (#865)."""

from __future__ import annotations

import numpy as np
import pytest

from dokimi.eval.sign_flip import ShiftTest, _exact_hits, sign_flip_interval, sign_flip_p


@pytest.mark.parametrize("big", [1e16, 1e300])
def test_a_large_delta_does_not_hide_the_small_ones_at_the_ends(big):
    row = [big] + [1.0] * 19  # 20 deltas: Monte Carlo, and 1.0 vanishes next to big in a float sum
    p = sign_flip_p(row)
    lo, _ = sign_flip_interval(row, p, rounded=False)
    # the small deltas are the sign-flip evidence: shifting by 1 ties them all, so the lower end is 1, not ~0
    assert (p, lo) == (pytest.approx(5e-5, abs=1e-6), pytest.approx(1.0, abs=1e-6))
    test = ShiftTest(row)
    assert test.p(0.0) == sign_flip_p(row, exact=True)


def test_exact_resamples_of_deltas_of_both_signs_and_several_limbs_match_sign_flip_p():
    row = [-1e30, 7.5, -3.25, 2.0, -1.0, 4.5, 1e-3, -6.0, 8.0, 0.5, -2.5, 3.0, 1e20, -9.0, 5.0, 1.5, -4.0, 6.5, 2.5]
    for seed in (0, 3):
        assert ShiftTest(row, seed).p(0.0) == sign_flip_p(row, seed=seed, exact=True)


def test_tied_deltas_do_not_widen_the_slack_of_the_exact_resamples():
    row = [4096.0] + [2e-9] * 7 + [2.1e-9] + [-2e-9] * 8 + [0.0] * 300  # the zeros tie, and 17 deltas move
    assert ShiftTest(row).p(0.0) == sign_flip_p(row, exact=True)


def test_a_row_hash_collision_counts_each_row_on_its_own_sum():
    signs = np.array([[1.0, -1.0, 1.0], [-1.0, 1.0, 1.0]])  # the two rows share a row hash, with different sums
    assert _exact_hits(signs, [3.0, 2.0**60, 1.0], 6.0) == 1
