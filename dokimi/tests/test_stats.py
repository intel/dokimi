# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the paired-statistics helpers used by the N-arm comparison."""

import itertools
import pytest

from dokimi.eval import stats
from dokimi.eval.interactions import (
    detect_factorial_cells, interaction_significance, interaction_test,
)


# ---------------------------------------------------------------------------
# effect_label / adjust_pvalues
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "d,expected",
    [
        (None, "unknown"),
        (0.0, "negligible"),
        (0.19, "negligible"),
        (0.2, "small"),
        (-0.49, "small"),
        (0.5, "medium"),
        (-0.79, "medium"),
        (0.8, "large"),
        (-2.0, "large"),
    ],
)
def test_effect_label_bands(d, expected):
    assert stats.effect_label(d) == expected


def test_holm_is_step_down_and_monotone():
    # Smallest p gets the largest multiplier (m), the largest gets 1x.
    adj = stats.adjust_pvalues([0.01, 0.04, 0.03], method="holm")
    assert adj[0] == pytest.approx(0.03)  # 0.01 * 3
    assert adj[2] == pytest.approx(0.06)  # 0.03 * 2
    assert adj[1] == pytest.approx(0.06)  # 0.04 * 1, raised to stay monotone
    # Monotonicity in p-order must hold.
    assert adj[0] <= adj[2] <= adj[1]


def test_bh_is_less_conservative_than_holm():
    pvals = [0.01, 0.02, 0.03, 0.04]
    holm = stats.adjust_pvalues(pvals, method="holm")
    bh = stats.adjust_pvalues(pvals, method="bh")
    assert all(b <= h for b, h in zip(bh, holm, strict=True))


def test_adjust_pvalues_caps_at_one_and_preserves_order():
    adj = stats.adjust_pvalues([0.9, 0.8], method="holm")
    assert adj == [1.0, 1.0]


def test_adjust_pvalues_skips_none_without_inflating_family():
    # Family size is 1, so the single real p-value is unchanged.
    adj = stats.adjust_pvalues([None, 0.02, None], method="holm")
    assert adj[0] is None and adj[2] is None
    assert adj[1] == pytest.approx(0.02)


def test_adjust_pvalues_rejects_unknown_method():
    with pytest.raises(ValueError):
        stats.adjust_pvalues([0.1], method="sidak")


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------


_QIDS = itertools.count()


def _ev(**arms):
    """One arms.v1 evaluation as ``ArmRunner.judge`` writes it (an id, a score block per arm)."""
    return {"question_id": f"q{next(_QIDS)}",
            "scores": {k: {"aggregate": v} for k, v in arms.items() if v is not None}}


def test_paired_vectors_keeps_complete_cases_only():
    evaluations = [
        _ev(baseline=50, skill=60),
        _ev(baseline=40, skill=None),  # skill missing -> dropped
        _ev(baseline=None, skill=70),  # baseline missing -> dropped
        _ev(baseline=55, skill=65),
    ]
    treat, base = stats.paired_vectors(evaluations, "skill", "baseline")
    assert treat == [60.0, 65.0]
    assert base == [50.0, 55.0]


def test_paired_vectors_ignores_malformed_score_blocks():
    evaluations = [
        {"question_id": "m1", "scores": {"baseline": {"aggregate": "n/a"}, "skill": {"aggregate": 60}}},
        {"question_id": "m2", "scores": {"baseline": None, "skill": {"aggregate": 60}}},
        _ev(baseline=50, skill=60),
    ]
    treat, base = stats.paired_vectors(evaluations, "skill", "baseline")
    assert treat == [60.0] and base == [50.0]


# ---------------------------------------------------------------------------
# paired_test
# ---------------------------------------------------------------------------


def test_paired_test_reports_delta_effect_and_ci():
    control = [50, 52, 48, 51, 49, 50]
    treatment = [60, 62, 58, 61, 59, 60]  # uniform +10
    res = stats.paired_test(treatment, control)
    assert res["n"] == 6
    assert res["delta_mean"] == pytest.approx(10.0)
    assert res["delta_std"] == pytest.approx(0.0)
    # Zero variance with a non-zero shift: d_z is unbounded, so it is reported
    # as unknown rather than 0.0 — which would have read as "negligible" for a
    # perfectly consistent +10 shift.
    assert res["cohens_d"] is None
    assert res["effect"] == "unknown"
    assert res["ci_lo"] == pytest.approx(10.0)
    assert res["ci_hi"] == pytest.approx(10.0)


def test_identical_scores_are_a_genuine_zero_effect():
    res = stats.paired_test([50, 51, 52], [50, 51, 52])
    assert res["delta_mean"] == 0.0
    assert res["cohens_d"] == 0.0
    assert res["effect"] == "negligible"


def test_paired_test_suppresses_pvalues_on_zero_variance_deltas():
    # A constant delta makes t infinite; SciPy would report p == 0, which must
    # not be surfaced as overwhelming significance.
    res = stats.paired_test([60] * 6, [50] * 6)
    assert res["delta_std"] == pytest.approx(0.0)
    assert res["p_ttest"] is None
    assert res["p_wilcoxon"] is None
    # None, not False: no test ran, so "not significant" would be a claim we
    # cannot make. A constant +10 shift is not evidence of no difference.
    assert res["significant"] is None


def test_paired_test_detects_a_real_effect_when_scipy_present():
    pytest.importorskip("scipy")
    control = [50, 52, 48, 51, 49, 50, 53, 47]
    treatment = [62, 65, 59, 64, 60, 63, 66, 58]
    res = stats.paired_test(treatment, control)
    assert res["delta_mean"] > 10
    assert res["p_ttest"] < 0.05
    assert res["significant"] is True
    assert res["effect"] == "large"
    # CI on a clear effect must exclude zero.
    assert res["ci_lo"] > 0


def test_paired_test_finds_no_effect_in_noise():
    pytest.importorskip("scipy")
    control = [50, 55, 45, 60, 40, 52, 48, 58]
    treatment = [51, 54, 46, 59, 41, 51, 49, 57]
    res = stats.paired_test(treatment, control)
    assert res["significant"] is False


def test_paired_test_rejects_mismatched_or_tiny_input():
    assert stats.paired_test([1, 2], [1]) is None
    assert stats.paired_test([1], [1]) is None
    assert stats.paired_test([], []) is None


def test_paired_test_skips_pvalues_below_minimum_pairs():
    # 4 pairs is under MIN_PAIRS_FOR_TEST, so no p-values, but CI still returned.
    res = stats.paired_test([60, 61, 62, 63], [50, 51, 52, 53])
    assert res["n"] == 4
    assert res["p_ttest"] is None
    assert res["significant"] is None
    assert res["delta_mean"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# arm_significance
# ---------------------------------------------------------------------------


def _factorial_evaluations():
    """Six questions x four arms; skill helps a lot, docs barely.

    Per-question jitter keeps the deltas from being constant, so the
    hypothesis tests are exercised rather than short-circuited by the
    zero-variance guard. Mean deltas remain exactly +2 / +12 / +6.
    """
    base = [50, 52, 48, 51, 49, 53]
    jitter = [1, -1, 2, -2, 1, -1]  # sums to zero
    docs = [b + 2 + j for b, j in zip(base, jitter, strict=True)]
    skill = [b + 12 - j for b, j in zip(base, jitter, strict=True)]
    combined = [b + 6 + j for b, j in zip(base, jitter, strict=True)]  # sub-additive
    return [
        _ev(baseline=b, docs=d, **{"skill:x": s, "docs+skill:x": c})
        for b, d, s, c in zip(base, docs, skill, combined, strict=True)
    ]


def test_arm_significance_shares_one_baseline_across_arms():
    evaluations = _factorial_evaluations()
    res = stats.arm_significance(
        evaluations, "baseline", ["baseline", "docs", "skill:x", "docs+skill:x"]
    )
    assert "baseline" not in res  # baseline is not tested against itself
    assert set(res) == {"docs", "skill:x", "docs+skill:x"}
    # Deltas are exact because every arm was paired to the same control.
    assert res["docs"]["delta_mean"] == pytest.approx(2.0)
    assert res["skill:x"]["delta_mean"] == pytest.approx(12.0)
    assert res["docs+skill:x"]["delta_mean"] == pytest.approx(6.0)
    # Every arm reports the same family size and correction method.
    for sig in res.values():
        assert sig["correction"] == "holm"
        assert sig["family_size"] == 3
        # Without SciPy there are no p-values to correct, but the family of
        # arms is still reported so the artifact records what was compared.
        assert sig["family_tested"] == (3 if stats.HAS_SCIPY else 0)


def test_arm_significance_infers_arms_when_not_given():
    res = stats.arm_significance(_factorial_evaluations(), "baseline")
    assert set(res) == {"docs", "skill:x", "docs+skill:x"}


def test_arm_significance_adjusted_p_is_never_below_raw():
    pytest.importorskip("scipy")
    res = stats.arm_significance(_factorial_evaluations(), "baseline")
    for sig in res.values():
        assert sig["p_adjusted"] >= sig["p_ttest"]
        # A corrected verdict can only be stricter than the uncorrected one.
        assert not (sig["significant_adjusted"] and not sig["significant"])


def test_arm_significance_bh_correction_selectable():
    res = stats.arm_significance(
        _factorial_evaluations(), "baseline", correction="bh"
    )
    assert all(sig["correction"] == "bh" for sig in res.values())


# ---------------------------------------------------------------------------
# factorial detection + interaction
# ---------------------------------------------------------------------------


def test_detect_factorial_cells_finds_combined_arm():
    arms = ["baseline", "docs", "skill:demo-skill", "docs+skill:demo-skill"]
    assert detect_factorial_cells(arms) == [
        ("docs", "skill:demo-skill", "docs+skill:demo-skill")
    ]


def test_detect_factorial_cells_requires_both_standalone_arms():
    # "docs" is present but the standalone skill arm is not -> not a 2x2 cell.
    assert detect_factorial_cells(["baseline", "docs", "docs+skill:x"]) == []


def test_detect_factorial_cells_ignores_three_way_combinations():
    arms = ["baseline", "a", "b", "c", "a+b+c"]
    assert detect_factorial_cells(arms) == []


def test_interaction_test_reports_negative_interference():
    res = interaction_test(
        _factorial_evaluations(),
        arm_a="docs",
        arm_b="skill:x",
        combined_arm="docs+skill:x",
        baseline_arm="baseline",
    )
    assert res["delta_a"] == pytest.approx(2.0)
    assert res["delta_b"] == pytest.approx(12.0)
    assert res["delta_combined"] == pytest.approx(6.0)
    assert res["additive_expectation"] == pytest.approx(14.0)
    # 6 - 14 = -8: combining the treatments loses 8 points against the sum.
    assert res["interaction"] == pytest.approx(-8.0)
    assert res["n"] == 6


def test_interaction_test_is_zero_for_perfectly_additive_arms():
    base = [50, 52, 48, 51, 49, 53]
    evaluations = [
        _ev(baseline=b, docs=b + 3, **{"skill:x": b + 7, "docs+skill:x": b + 10})
        for b in base
    ]
    res = interaction_test(
        evaluations,
        arm_a="docs",
        arm_b="skill:x",
        combined_arm="docs+skill:x",
        baseline_arm="baseline",
    )
    assert res["interaction"] == pytest.approx(0.0)
    assert res["additive_expectation"] == pytest.approx(res["delta_combined"])


def test_interaction_test_needs_all_four_cells_per_question():
    # Only two questions have all four arms scored.
    evaluations = [
        _ev(baseline=50, docs=52, **{"skill:x": 62, "docs+skill:x": 56}),
        _ev(baseline=52, docs=54, **{"skill:x": 64, "docs+skill:x": 58}),
        _ev(baseline=48, docs=50, **{"skill:x": 60}),  # combined missing
    ]
    res = interaction_test(
        evaluations,
        arm_a="docs",
        arm_b="skill:x",
        combined_arm="docs+skill:x",
        baseline_arm="baseline",
    )
    assert res["n"] == 2


def test_interaction_test_returns_none_without_enough_complete_cases():
    evaluations = [_ev(baseline=50, docs=52, **{"skill:x": 62, "docs+skill:x": 56})]
    assert (
        interaction_test(
            evaluations,
            arm_a="docs",
            arm_b="skill:x",
            combined_arm="docs+skill:x",
            baseline_arm="baseline",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Regression guards for behaviour that mutation testing found unpinned
# ---------------------------------------------------------------------------


def test_cohens_d_uses_the_sample_sd_not_the_population_sd():
    """Pins d_z numerically, so a silent ddof=1 -> ddof=0 drift fails here.

    Every other assertion in this file checks the coarse `effect` band, which
    the sqrt((n-1)/n) factor does not move at these sample sizes.
    """
    # Deltas: +10, +20, +30, +10, +20, +30. mean = 20, ddof=1 SD = 8.94427.
    control = [50] * 6
    treatment = [60, 70, 80, 60, 70, 80]
    res = stats.paired_test(treatment, control)
    assert res["delta_std"] == pytest.approx(8.94, abs=0.005)
    # 20 / 8.94427 = 2.236. The population SD (8.1650) would give 2.449.
    assert res["cohens_d"] == pytest.approx(2.236, abs=0.001)


def test_bh_enforces_step_up_monotonicity():
    """A p-vector where the cumulative min actually bites.

    Raw BH for the 0.06 entry is 0.06 * 4/4 = 0.06 while the 0.04 entry at rank
    3 adjusts to 0.0533; without the running minimum the sorted output would not
    be monotone. The existing BH tests use a vector where every raw value lands
    on the same number, so the step is a no-op there.
    """
    raw = [0.01, 0.06, 0.03, 0.04]
    adjusted = stats.adjust_pvalues(raw, method="bh")
    by_raw = [adj for _, adj in sorted(zip(raw, adjusted, strict=True))]
    assert by_raw == sorted(by_raw)
    assert adjusted[1] == pytest.approx(0.06)
    assert adjusted[2] == pytest.approx(0.0533, abs=0.0001)
    assert adjusted[3] == pytest.approx(0.0533, abs=0.0001)


def test_bootstrap_ci_is_pinned_to_its_seed():
    """Locks the RNG stream, not merely that f(seed) equals itself.

    A same-seed-twice comparison cannot detect the seed being offset or the
    resampling loop changing shape, so committed CI numbers were never locked.
    """
    vals = [10, 12, 8, 11, 9, 30]
    ci = stats.bootstrap_ci(vals, seed=0, n_resamples=1000)
    assert ci["method"] == "percentile_bootstrap"
    assert ci["mean"] == pytest.approx(13.3333, abs=0.0001)
    pinned = (ci["lo"], ci["hi"])
    assert pinned == (9.1667, 20.0)
    # A different seed must move the interval; identical bounds would mean the
    # seed never reaches the resampler.
    other = stats.bootstrap_ci(vals, seed=7, n_resamples=1000)
    assert (other["lo"], other["hi"]) != pinned
    # And the same seed must still reproduce it exactly.
    assert (stats.bootstrap_ci(vals, seed=0, n_resamples=1000)["lo"],
            stats.bootstrap_ci(vals, seed=0, n_resamples=1000)["hi"]) == pinned


def test_bootstrap_percentile_indices_are_symmetric():
    """Equal numbers of resamples must fall outside each tail."""
    vals = list(range(1, 21))
    ci = stats.bootstrap_ci(vals, confidence=0.90, seed=3, n_resamples=1000)
    mean = sum(vals) / len(vals)
    assert ci["lo"] < mean < ci["hi"]
    assert abs((mean - ci["lo"]) - (ci["hi"] - mean)) < 0.5


def test_mean_ci_is_coherent_with_the_t_test():
    """The interval must exclude zero exactly when p_ttest rejects.

    A bootstrap interval beside a t p-value can disagree, which reads as a
    rendering bug rather than as the two different inferences it really is.
    """
    pytest.importorskip("scipy")
    control = [50, 52, 48, 51, 49, 50, 53, 47]
    treatment = [53, 54, 50, 54, 51, 52, 56, 49]
    res = stats.paired_test(treatment, control)
    assert res["ci_method"] == "student_t"
    excludes_zero = bool(res["ci_lo"] > 0 or res["ci_hi"] < 0)
    assert excludes_zero == (res["p_ttest"] < stats.ALPHA)
    # And the reverse direction, on a sample the t-test does not reject.
    noise = stats.paired_test([51, 54, 46, 59, 41, 51, 49, 57],
                              [50, 55, 45, 60, 40, 52, 48, 58])
    assert noise["p_ttest"] > stats.ALPHA
    assert noise["ci_lo"] < 0 < noise["ci_hi"]


def test_mean_ci_falls_back_and_collapses_on_degenerate_input():
    assert stats.mean_ci([])["method"] == "none"
    degenerate = stats.mean_ci([7.0] * 5)
    assert (degenerate["lo"], degenerate["hi"]) == (7.0, 7.0)
    assert degenerate["method"] == "degenerate"


@pytest.mark.parametrize("confidence", [0.0, 1.0, 1.5, -0.2])
def test_mean_ci_rejects_a_confidence_outside_zero_one(confidence):
    """The Student-t path must refuse what the bootstrap path refuses.

    Without the check, ``confidence=1.5`` reaches ``t.ppf`` outside (0, 1) and
    the interval comes back as ``nan`` bounds instead of an error.
    """
    with pytest.raises(ValueError, match="confidence"):
        stats.mean_ci([50.0, 52.0, 48.0, 51.0], confidence=confidence)


def test_near_zero_variance_is_treated_as_degenerate():
    """Float noise must not become an "effect: large" verdict.

    Judge aggregates are weighted means, so a constant shift computed in
    floating point lands at sd ~ 1e-15, which an exact ``sd == 0.0`` guard lets
    through as d_z ~ 1e13 and p ~ 1e-68.
    """
    control = [50.0] * 6
    treatment = [60.0, 60.0, 60.0, 60.0, 60.0, 60.0 + 1e-13]
    res = stats.paired_test(treatment, control)
    assert res["cohens_d"] is None
    assert res["effect"] == "unknown"
    assert res["p_ttest"] is None
    assert res["significant"] is None


def test_wilcoxon_keeps_tied_pairs_in_the_sample():
    """zsplit, because SciPy's default silently drops tied pairs.

    Dropping them runs the test on a fraction of the data while ``n`` still
    reports the full count, which can flip the verdict.
    """
    pytest.importorskip("scipy")
    control = [50] * 20
    treatment = [50] * 15 + [51, 52, 53, 54, 55]  # 15 ties, 5 positive deltas
    res = stats.paired_test(treatment, control)
    assert res["n"] == 20
    assert res["n_wilcoxon"] == 20
    # Discarding the ties runs the test on 5 of 20 pairs and yields p = 0.0431,
    # i.e. significant. Keeping them in the rank sum gives 0.0770 — the verdict
    # flips, and only the second answer matches the n the artifact reports.
    assert res["p_wilcoxon"] == pytest.approx(0.077, abs=0.001)
    assert res["p_wilcoxon"] > stats.ALPHA


def test_correction_runs_on_unrounded_pvalues():
    """Holm must multiply the raw p, not its 4-decimal rendering.

    ``3 * round(0.0166666, 4) = 0.0501`` (not significant) versus the exact
    0.05 boundary — rounding first can flip the verdict.
    """
    adjusted = stats.adjust_pvalues([0.0166666, 0.5, 0.9], method="holm")
    assert adjusted[0] == pytest.approx(0.05, abs=1e-4)
    assert adjusted[0] < 0.0501


def test_detect_factorial_cells_handles_a_plus_inside_an_arm_name():
    """``skill:c++-guide`` must not defeat cell detection.

    Splitting on every "+" yields four parts here, so a ``len(parts) == 2``
    guard silently reports no interaction for an ordinary arm name.
    """
    arms = ["baseline", "docs", "skill:c++-guide", "docs+skill:c++-guide"]
    assert detect_factorial_cells(arms) == [
        ("docs", "skill:c++-guide", "docs+skill:c++-guide")
    ]


def test_interaction_significance_corrects_within_its_own_family():
    evaluations = _factorial_evaluations()
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    results = interaction_significance(evaluations, arms, "baseline")
    assert len(results) == 1
    res = results[0]
    assert res["correction_family"] == "interactions"
    assert res["family_size"] == 1
    # Single-member family: the adjusted p equals the raw one.
    assert res["p_adjusted"] == pytest.approx(res["p_ttest"])
    assert res["significant_adjusted"] is not None


def test_interaction_family_is_not_pooled_with_the_arm_family():
    """Two independent families, so neither inflates the other's multiplier."""
    evaluations = _factorial_evaluations()
    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    per_arm = stats.arm_significance(evaluations, "baseline", arms)
    interactions = interaction_significance(evaluations, arms, "baseline")
    assert per_arm["docs"]["family_size"] == 3
    assert per_arm["docs"]["correction_family"] == "arms"
    assert interactions[0]["family_size"] == 1


def test_interaction_test_alone_reports_no_adjusted_keys():
    """One cell in isolation carries no multiplicity to correct for."""
    res = interaction_test(
        _factorial_evaluations(),
        arm_a="docs",
        arm_b="skill:x",
        combined_arm="docs+skill:x",
        baseline_arm="baseline",
    )
    assert "p_adjusted" not in res
    assert "significant_adjusted" not in res


def test_arm_significance_leaves_the_adjusted_verdict_unknown_when_no_test_ran():
    """None, not False: an untestable arm is not a demonstrated null."""
    evaluations = [
        _ev(baseline=50, docs=60, **{"skill:x": 50 + i}) for i in range(6)
    ]
    res = stats.arm_significance(
        evaluations, "baseline", ["baseline", "docs", "skill:x"]
    )
    # `docs` is a constant +10 shift -> degenerate, so no test could run.
    assert res["docs"]["p_ttest"] is None
    assert res["docs"]["p_adjusted"] is None
    assert res["docs"]["significant_adjusted"] is None
    # The untestable arm must not inflate the tested arm's multiplier.
    assert res["skill:x"]["family_size"] == 2
    assert res["skill:x"]["family_tested"] == 1


# ---------------------------------------------------------------------------
# Pinned values at the boundaries (mutation pilot: each test below kills a
# surviving mutant of a comparison, an off-by-one or a dropped term)
# ---------------------------------------------------------------------------


def test_a_wilcoxon_above_alpha_vetoes_a_significant_t_test():
    """One outlier keeps the signed-rank test above 0.05 while the t-test is below."""
    pytest.importorskip("scipy")
    res = stats.paired_test([10, 11, 12, 10, 11, -1], [0] * 6)
    assert res["p_ttest"] == 0.0068
    assert res["p_wilcoxon"] == 0.0625
    assert res["n_wilcoxon"] == 6
    assert res["significant"] is False


def test_min_pairs_for_test_is_the_smallest_n_that_can_be_significant():
    """An exact two-sided Wilcoxon cannot reach 0.05 at five pairs (smallest p 0.0625)."""
    pytest.importorskip("scipy")
    assert stats.MIN_PAIRS_FOR_TEST == 6
    five = stats.paired_test([1.0, 2.0, 3.0, 4.0, 5.0], [0.0] * 5)
    assert (five["n"], five["p_ttest"], five["p_wilcoxon"], five["significant"]) == (5, None, None, None)
    six = stats.paired_test([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [0.0] * 6)
    assert six["p_wilcoxon"] == 0.0312


def test_below_min_pairs_no_test_runs():
    pytest.importorskip("scipy")
    n = stats.MIN_PAIRS_FOR_TEST - 1
    res = stats.paired_test([float(i) for i in range(n)], [0.5 * i for i in range(n)])
    assert res["n"] == n
    assert (res["p_ttest"], res["p_wilcoxon"], res["n_wilcoxon"]) == (None, None, 0)
    assert res["significant"] is None


def test_two_pairs_are_enough_for_a_description():
    res = stats.paired_test([1.0, 3.0], [0.0, 0.0])
    assert res["n"] == 2
    assert res["delta_mean"] == 2.0
    assert res["cohens_d"] == 1.414


def test_bootstrap_interval_uses_the_two_sided_alpha():
    # 10,000 resamples of [0, 1]: the 30th and 70th percentiles of the
    # resampled means are both 0.5 at confidence 0.4.
    res = stats.bootstrap_ci([0, 1], confidence=0.4)
    assert (res["lo"], res["hi"]) == (0.5, 0.5)


def test_bootstrap_interval_pins_the_tail_indices_and_the_seed():
    # A sample whose resampled means spread out, so a one-index shift in either tail, a
    # one-sided alpha, or another seed each moves the bounds (for [0, 1] they would not).
    # 100 resamples at 0.8: alpha * 99 = 9.9, so the low index rounds to 10, not 9.
    ci = stats.bootstrap_ci([0, 1, 2, 3, 10, 4.5, 7.5], confidence=0.8, n_resamples=100, seed=7)
    assert (ci["lo"], ci["hi"]) == (2.0714, 5.7857)
    wide = stats.bootstrap_ci([0, 1, 2, 3, 10], confidence=0.8, n_resamples=200, seed=7)
    assert (wide["lo"], wide["hi"]) == (1.2, 5.0)


@pytest.mark.parametrize(
    ("confidence", "n_resamples", "expected"),
    [(0.95, 100, (1.2143, 6.5714)), (0.5, 50, (3.0, 4.9286))],
)
def test_bootstrap_interval_tails_are_symmetric_order_statistics(confidence, n_resamples, expected):
    # Literals from the original code. Dropping round() from either index, or using n_resamples
    # instead of n_resamples - 1, moves a bound by one order statistic on these (and not on the case above).
    ci = stats.bootstrap_ci([0, 1, 2, 3, 10, 4.5, 7.5], confidence=confidence, n_resamples=n_resamples, seed=7)
    assert (ci["lo"], ci["hi"]) == expected


def test_paired_test_rejects_a_length_mismatch_in_both_directions():
    assert stats.paired_test([1, 2, 3], [1, 2, 3, 4]) is None  # control longer
    assert stats.paired_test([1, 2, 3, 4], [1, 2, 3]) is None  # treatment longer


def test_mean_ci_forwards_the_confidence_and_the_seed_to_the_bootstrap_fallback(monkeypatch):
    seen = {}

    def fake(vals, confidence=None, **kw):
        seen.update(kw, confidence=confidence)
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0, "n": len(vals)}

    monkeypatch.setattr(stats, "HAS_SCIPY", False)
    monkeypatch.setattr(stats, "bootstrap_ci", fake)
    stats.mean_ci([1.0, 2.0, 3.0], confidence=0.8, seed=7)
    assert seen == {"seed": 7, "confidence": 0.8}


def test_bootstrap_ci_defaults_are_pinned():
    """grounding.py calls bootstrap_ci with every default, so the default seed and
    n_resamples set the stored grounding_score interval."""
    ci = stats.bootstrap_ci([0.12, 0.47, 0.83, 0.31, 0.95, 0.58, 0.06, 0.74, 0.29, 0.66, 0.41, 0.88])
    assert (ci["lo"], ci["hi"]) == (0.3617, 0.6842)


def test_bootstrap_with_one_resample_collapses_to_it():
    # Seed 0 draws index 1 twice: the one resample's mean is 1.0, and both
    # clamped indices read it. A seed or resampling change moves that literal.
    res = stats.bootstrap_ci([0, 1], n_resamples=1)
    assert (res["lo"], res["hi"]) == (1.0, 1.0)
    assert res["n"] == 2


@pytest.mark.parametrize("confidence", [0.0, 1.0, 1.5])
def test_bootstrap_rejects_a_confidence_outside_the_open_interval(confidence):
    with pytest.raises(ValueError, match="confidence"):
        stats.bootstrap_ci([0, 1], confidence=confidence)


def test_empty_and_single_samples_have_exact_shapes():
    assert stats.bootstrap_ci([]) == {"mean": None, "lo": None, "hi": None, "n": 0}
    assert stats.bootstrap_ci([2.5]) == {"mean": 2.5, "lo": 2.5, "hi": 2.5, "n": 1}
    assert stats.mean_ci([]) == {
        "mean": None, "lo": None, "hi": None, "n": 0, "method": "none"
    }
    assert stats.mean_ci([5.0]) == {"mean": 5.0, "lo": 5.0, "hi": 5.0, "n": 1}


def test_zero_variance_tolerance_scales_with_the_mean():
    """At a mean of 1e6, noise of 1e-4 is below the relative tolerance (1e-3) but
    far above an absolute one (1e-9), so only the scaled tolerance calls it degenerate."""
    pytest.importorskip("scipy")
    vals = [1e6 + (1e-4 if i % 2 else -1e-4) for i in range(8)]
    res = stats.mean_ci(vals)
    assert res["method"] == "degenerate"
    assert res["n"] == 8


def test_zero_variance_tolerance_is_relative_not_absolute():
    """The same noise is degenerate at a mean of 1e6 (tolerance 1e-3) and not at a mean of 1."""
    pytest.importorskip("scipy")
    big = [1e6 + (1e-4 if i % 2 else -1e-4) for i in range(8)]
    small = [1.0 + (1e-4 if i % 2 else -1e-4) for i in range(8)]
    assert stats.mean_ci(big)["method"] == "degenerate"
    assert stats.mean_ci(small)["method"] == "student_t"


def test_adjusted_pvalues_are_rounded_to_four_places_by_default():
    assert stats.adjust_pvalues([0.0123456]) == [0.0123]


def test_paired_test_rounds_the_wilcoxon_p():
    pytest.importorskip("scipy")
    res = stats.paired_test([3, 5, 4, 6, 2, 7, 5], [1, 2, 2, 1, 2, 3, 1])
    assert res["p_wilcoxon"] == round(res["p_wilcoxon"], 4)
    assert 0 < res["p_wilcoxon"] < 1


@pytest.mark.parametrize(
    "flags,totals,n_pass,reason",
    [
        ([True, False], [1, 2], 1, "fewer than 3 respondents"),
        ([True] * 3, [1, 2, 3], 3,
         "no variance in the item: every respondent scored it the same"),
        ([True, False, True], [2, 2, 2], 2,
         "no variance in the totals: nothing for the item to discriminate"),
    ],
)
def test_point_biserial_says_why_it_is_undefined(flags, totals, n_pass, reason):
    res = stats.point_biserial(flags, totals)
    assert res["r"] is None
    assert res["n"] == len(flags)
    assert res["n_pass"] == n_pass
    assert res["reason"] == reason


def test_arm_significance_passes_the_confidence_through():
    pytest.importorskip("scipy")
    wide = stats.arm_significance(_factorial_evaluations(), "baseline")
    narrow = stats.arm_significance(_factorial_evaluations(), "baseline", confidence=0.8)
    for arm in wide:
        assert wide[arm]["ci_confidence"] == 0.95
        assert narrow[arm]["ci_confidence"] == 0.8
        assert wide[arm]["ci_lo"] < narrow[arm]["ci_lo"] < narrow[arm]["ci_hi"] < wide[arm]["ci_hi"]


def test_arm_significance_includes_an_arm_with_two_pairs():
    res = stats.arm_significance(_factorial_evaluations()[:2], "baseline")
    assert set(res) == {"docs", "skill:x", "docs+skill:x"}
    assert all(sig["n"] == 2 for sig in res.values())


def test_arm_significance_leaves_out_an_arm_with_fewer_than_two_pairs():
    assert stats.arm_significance(_factorial_evaluations()[:1], "baseline") == {}
    assert stats.arm_significance([], "baseline") == {}


def test_arm_significance_correction_changes_the_adjusted_p():
    pytest.importorskip("scipy")
    holm = stats.arm_significance(_factorial_evaluations(), "baseline")
    bh = stats.arm_significance(_factorial_evaluations(), "baseline", correction="bh")
    # Holm multiplies the second-smallest p by 2, BH by 3/2.
    assert holm["docs+skill:x"]["p_adjusted"] == 0.0004
    assert bh["docs+skill:x"]["p_adjusted"] == 0.0003
    assert holm["skill:x"]["significant_adjusted"] is True
    assert holm["docs"]["significant_adjusted"] is False
