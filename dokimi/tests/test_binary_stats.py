# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for McNemar's exact test on the execution-verified task track."""

import math

import pytest

from dokimi.eval import binary_stats
from dokimi.eval.binary_stats import (
    exact_binomial_two_sided,
    mcnemar_test,
)


# ---------------------------------------------------------------------------
# exact_binomial_two_sided
# ---------------------------------------------------------------------------


def test_no_trials_is_no_evidence_not_certainty():
    """Zero discordant pairs means the data cannot speak, not p == 1."""
    assert exact_binomial_two_sided(0, 0) is None


@pytest.mark.parametrize(
    "successes,trials,expected",
    [
        (1, 1, 1.0),          # 2 * 1/2
        (2, 2, 0.5),          # 2 * 1/4
        (3, 3, 0.25),
        (4, 4, 0.125),
        (5, 5, 0.0625),
        (6, 6, 0.03125),      # first count that clears 0.05 with zero losses
        (1, 2, 1.0),          # perfectly split, capped at 1.0
    ],
)
def test_exact_tail_matches_hand_computation(successes, trials, expected):
    assert exact_binomial_two_sided(successes, trials) == pytest.approx(expected)


def test_exact_tail_is_symmetric():
    for trials in range(1, 12):
        for k in range(trials + 1):
            assert exact_binomial_two_sided(k, trials) == pytest.approx(
                exact_binomial_two_sided(trials - k, trials)
            )


def test_exact_tail_matches_scipy_when_available():
    scipy_stats = pytest.importorskip("scipy.stats")
    for trials, k in [(7, 6), (11, 3), (20, 14), (13, 13)]:
        expected = scipy_stats.binomtest(k, trials, 0.5).pvalue
        assert exact_binomial_two_sided(k, trials) == pytest.approx(expected)


def test_exact_tail_rejects_impossible_counts():
    with pytest.raises(ValueError):
        exact_binomial_two_sided(3, 2)
    with pytest.raises(ValueError):
        exact_binomial_two_sided(-1, 2)


def test_exact_tail_never_exceeds_one():
    # Doubling the smaller tail can overshoot at small trials; it must be capped.
    assert exact_binomial_two_sided(1, 2) <= 1.0
    assert exact_binomial_two_sided(2, 4) <= 1.0


# ---------------------------------------------------------------------------
# mcnemar_test — the 2x2 table
# ---------------------------------------------------------------------------


def test_counts_split_into_the_four_cells():
    treat =   [True,  False, True,  False, True]
    control = [False, True,  True,  False, False]
    res = mcnemar_test(treat, control)
    assert res["n"] == 5
    assert res["n_gained"] == 2       # positions 0 and 4
    assert res["n_lost"] == 1         # position 1
    assert res["n_both_passed"] == 1  # position 2
    assert res["n_both_failed"] == 1  # position 3
    assert res["n_discordant"] == 3
    assert res["paired_pass_rate_delta"] == pytest.approx((2 - 1) / 5)


def test_concordant_tasks_do_not_change_the_pvalue():
    """The point of conditioning on discordance: easy tasks add no power."""
    treat = [True, True, True, False]
    control = [False, False, False, True]
    lean = mcnemar_test(treat, control)
    # Ten more tasks both arms already solve.
    padded = mcnemar_test(treat + [True] * 10, control + [True] * 10)
    assert padded["p_exact"] == lean["p_exact"]
    assert padded["n_both_passed"] == 10
    # The pass-rate delta *does* shrink — it is a rate over all tasks.
    assert abs(padded["paired_pass_rate_delta"]) < abs(lean["paired_pass_rate_delta"])


def test_a_clean_sweep_of_four_is_suggestive_not_significant():
    """4-0 gives p = 0.125. Reporting it as significant would be wrong."""
    res = mcnemar_test([True] * 4 + [False] * 6, [False] * 10)
    assert res["n_gained"] == 4 and res["n_lost"] == 0
    assert res["p_exact"] == pytest.approx(0.125)
    assert res["significant"] is False


def test_six_gains_and_no_losses_clears_alpha():
    res = mcnemar_test([True] * 6, [False] * 6)
    # 2/2**6 = 0.03125, stored at the 4-decimal precision the artifact uses.
    assert res["p_exact"] == pytest.approx(0.03125, abs=1e-4)
    assert res["significant"] is True


def test_identical_outcomes_report_no_evidence_rather_than_no_effect():
    res = mcnemar_test([True, False, True, False], [True, False, True, False])
    assert res["n_discordant"] == 0
    assert res["paired_pass_rate_delta"] == 0.0
    assert res["p_exact"] is None
    # Crucially not False: "we could not tell" != "we showed no difference".
    assert res["significant"] is None


def test_a_regression_is_reported_with_a_negative_delta():
    res = mcnemar_test([False] * 6, [True] * 6)
    assert res["n_lost"] == 6 and res["n_gained"] == 0
    assert res["paired_pass_rate_delta"] == pytest.approx(-1.0)
    # The test is two-sided, so a clean regression is just as significant.
    assert res["p_exact"] == pytest.approx(0.03125, abs=1e-4)
    assert res["significant"] is True


def test_equal_gains_and_losses_are_not_significant():
    res = mcnemar_test([True, True, False, False], [False, False, True, True])
    assert res["paired_pass_rate_delta"] == 0.0
    assert res["p_exact"] == pytest.approx(1.0)
    assert res["significant"] is False


def test_ci_brackets_the_delta_and_is_deterministic():
    treat = [True] * 5 + [False] * 5
    control = [False] * 10
    res = mcnemar_test(treat, control)
    assert res["ci_lo"] <= res["paired_pass_rate_delta"] <= res["ci_hi"]
    assert res["ci_confidence"] == 0.95
    assert mcnemar_test(treat, control) == res  # seeded bootstrap


def test_truthy_values_are_coerced_not_rejected():
    """Rows carry whatever the harness put in ``passed``; 0/1 must behave."""
    assert mcnemar_test([1, 0], [0, 0])["n_gained"] == 1


def test_rejects_mismatched_or_single_pair_input():
    assert mcnemar_test([True, False], [True]) is None
    assert mcnemar_test([True], [False]) is None
    assert mcnemar_test([], []) is None
    assert binary_stats.MIN_PAIRS_FOR_TEST == 2


def test_correction_fields_start_empty_for_the_caller_to_fill():
    res = mcnemar_test([True] * 6, [False] * 6)
    assert res["p_adjusted"] is None
    assert res["significant_adjusted"] is None
    assert res["test"] == "mcnemar_exact"


def test_small_pvalues_keep_significant_digits():
    """_round_p must not flatten a tiny exact p to 0.0 in the artifact."""
    res = mcnemar_test([True] * 20, [False] * 20)
    assert res["p_exact"] == pytest.approx(2 / 2 ** 20, rel=1e-2)
    assert res["p_exact"] > 0.0
    assert math.isfinite(res["p_exact"])


# Mutation pilot: the disclosure fields and the CI parameters reach the result.


def test_repeats_of_one_task_are_flagged_as_pseudo_replicated():
    res = mcnemar_test(
        [True, True, True, False], [False, False, True, False], units=["a", "a", "b", "b"]
    )
    assert (res["n"], res["n_units"]) == (4, 2)
    assert res["pseudo_replicated"] is True


def test_one_unit_per_pair_is_not_pseudo_replicated():
    res = mcnemar_test([True, True, False], [False, True, False], units=["a", "b", "c"])
    assert (res["n"], res["n_units"]) == (3, 3)
    assert res["pseudo_replicated"] is False


def test_a_units_list_of_the_wrong_length_is_rejected():
    with pytest.raises(ValueError, match="units must be one per pair: got 1 for 2 pairs"):
        mcnemar_test([True, False], [False, False], units=["a"])


def test_the_interval_follows_the_confidence_and_the_seed():
    treat = [True] * 6 + [False] * 4
    control = [False] * 3 + [True] * 3 + [False] * 4
    wide = mcnemar_test(treat, control)
    narrow = mcnemar_test(treat, control, confidence=0.5)
    assert narrow["ci_confidence"] == 0.5
    assert wide["ci_lo"] < narrow["ci_lo"] <= narrow["ci_hi"] < wide["ci_hi"]
    deltas = [1.0] * 3 + [0.0] * 7
    boot = binary_stats.bootstrap_ci(deltas, seed=7)
    seeded = mcnemar_test(treat, control, seed=7)
    assert (seeded["ci_lo"], seeded["ci_hi"]) == (boot["lo"], boot["hi"])


def test_the_seed_reaches_an_interval_that_depends_on_it():
    # 6 gained, 6 lost, 3 both-pass: the bootstrap bounds differ between seeds (the case above gives
    # (0.0, 0.6) for every seed, so it cannot tell a forwarded seed from a dropped one).
    treat = [True] * 6 + [False] * 6 + [True] * 3
    control = [False] * 6 + [True] * 6 + [True] * 3
    default = mcnemar_test(treat, control)
    seeded = mcnemar_test(treat, control, seed=7)
    assert (default["ci_lo"], default["ci_hi"]) == (-0.4, 0.4667)
    assert (seeded["ci_lo"], seeded["ci_hi"]) == (-0.4667, 0.4667)


def test_the_paired_delta_keeps_four_decimals():
    res = mcnemar_test([True, False, False], [False, False, False])
    assert res["paired_pass_rate_delta"] == 0.3333
