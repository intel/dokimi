# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Small statistics helpers for evaluation rigor.

Pure-Python by default: bootstrap CIs, effect sizes, and multiple-comparison
correction need no third-party packages. The parametric/rank hypothesis tests
(:func:`paired_test`, and :func:`dokimi.eval.interactions.interaction_test`)
additionally use SciPy and return ``p_*`` keys of ``None`` when it is not
installed — everything else in the result stays populated.

Public API
----------
bootstrap_ci(values, ...)
    Percentile bootstrap CI for the mean.
effect_label(d)
    Cohen's-d band name ("negligible" … "large").
adjust_pvalues(pvals, method)
    Holm-Bonferroni (default) or Benjamini-Hochberg adjusted p-values.
paired_vectors(evaluations, arm_a, arm_b)
    Per-question paired score vectors for two arms, complete cases only.
paired_summary(evaluations)
    Both arms and their delta over the complete pairs, with the unpaired count.
    Called by the eval report, the report generator and the pipeline summary; the
    dashboard and the model-compare run summary average both arms over the same pairs.
mean_ci(values, ...)
    Student-t CI for the mean, coherent with ``p_ttest``; bootstrap fallback.
point_biserial(flags, totals)
    Item-analysis discrimination: correlation of one binary item against the
    respondents' total scores.
paired_test(treatment, control, ...)
    Paired t-test + Wilcoxon + Cohen's d_z + CI on the deltas.
arm_significance(evaluations, baseline_arm, ...)
    Every non-baseline arm tested against one *shared* baseline, with
    family-wise correction across the arms.

The 2x2 interaction contrasts between arms (``detect_factorial_cells``,
``interaction_test``, ``interaction_significance``) are built on these helpers
in :mod:`dokimi.eval.interactions`; import them from there.

Everything here assumes a continuous outcome (the judge's 0–100 aggregate).
Paired **binary** outcomes — the execution-verified task track, where a cell
either passed its test suite or did not — belong in
:mod:`dokimi.eval.binary_stats`, which uses McNemar's exact test
instead.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dokimi.records import BASELINE, CONTEXT, RecordLike, records

try:
    from scipy import stats as _sp_stats

    HAS_SCIPY = True
except ImportError:  # pragma: no cover - environment dependent
    _sp_stats = None
    HAS_SCIPY = False

#: Minimum complete pairs before a hypothesis test is attempted.
#:
#: Six, not five, because :func:`_describe_deltas` requires *both* the t-test
#: and the Wilcoxon test to clear ALPHA. The smallest attainable two-sided exact
#: Wilcoxon p at n=5 is 0.0625 — above 0.05 — so at five pairs no data
#: whatsoever could produce a positive verdict. A threshold that admits an
#: unwinnable regime is worse than a slightly higher one.
MIN_PAIRS_FOR_TEST = 6

#: Default significance level.
ALPHA = 0.05

#: Accepted multiple-comparison corrections (see :func:`adjust_pvalues`).
CORRECTIONS = ("holm", "bh")

#: Relative tolerance for calling a delta vector's spread zero.
#:
#: An exact ``sd == 0.0`` test is a cliff edge: judge aggregates are weighted
#: means, so a constant shift computed in floating point often lands at
#: ``sd = 3e-15`` rather than exactly zero, which then yields ``d_z = 2.8e13``,
#: ``p = 1e-68`` and an "effect: large" verdict on what is really a degenerate
#: sample. Scaled by the mean so the test is unit-free.
ZERO_VARIANCE_REL_TOL = 1e-9


def _is_degenerate(mean: float, sd: float) -> bool:
    """True when ``sd`` is zero or indistinguishable from it at this scale."""
    return sd <= ZERO_VARIANCE_REL_TOL * max(1.0, abs(mean))


def bootstrap_ci(
    values, confidence: float = 0.95, n_resamples: int = 10_000, seed: int = 0
) -> dict:
    """Percentile bootstrap confidence interval for the mean of ``values``.

    Returns ``{"mean", "lo", "hi", "n"}``. Deterministic for a given ``seed``,
    so it is safe to use in tests and reproducible runs.

    Prefer :func:`mean_ci` on continuous data: the percentile bootstrap
    under-covers badly at the sample sizes this module targets (measured ~87.5%
    coverage of a nominal 95% interval at n=6, versus ~95.4% for the t
    interval). This function stays for discrete outcomes and as the
    SciPy-absent fallback.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1 (exclusive)")
    if n_resamples < 1:
        raise ValueError("n_resamples must be >= 1")

    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    if n == 1:
        return {"mean": round(vals[0], 4), "lo": round(vals[0], 4), "hi": round(vals[0], 4), "n": 1}

    rng = random.Random(seed)
    means = []
    for _ in range(n_resamples):
        sample_sum = sum(vals[rng.randrange(n)] for _ in range(n))
        means.append(sample_sum / n)
    means.sort()

    # Symmetric order statistics: the naive lo=int(a*B), hi=int((1-a)*B) pair
    # leaves a different number of resamples outside each tail.
    alpha = (1.0 - confidence) / 2.0
    lo_idx = max(0, int(round(alpha * (n_resamples - 1))))
    hi_idx = min(n_resamples - 1, int(round((1.0 - alpha) * (n_resamples - 1))))
    return {
        "mean": round(sum(vals) / n, 4),
        "lo": round(means[lo_idx], 4),
        "hi": round(means[hi_idx], 4),
        "n": n,
        "method": "percentile_bootstrap",
    }


def mean_ci(values, confidence: float = 0.95, seed: int = 0) -> dict:
    """Confidence interval for the mean: Student-t when SciPy is available.

    The t interval is both better calibrated at small n and *coherent* with the
    reported ``p_ttest`` — the interval excludes zero exactly when the t-test
    rejects. A bootstrap interval next to a t p-value can disagree (measured:
    5.7% of runs at n=10), which reads as a rendering bug rather than as the
    two different inferences it really is.

    Falls back to :func:`bootstrap_ci` without SciPy, and for degenerate
    samples where the interval collapses to the mean.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1 (exclusive)")
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0, "method": "none"}
    if not HAS_SCIPY or n < 2:
        return bootstrap_ci(vals, confidence=confidence, seed=seed)

    mu = _mean(vals)
    sd = _sample_sd(vals)
    if _is_degenerate(mu, sd):
        # Every value identical: the interval is the point itself, and a
        # bootstrap would report the same thing after 10k pointless resamples.
        return {"mean": round(mu, 4), "lo": round(mu, 4), "hi": round(mu, 4),
                "n": n, "method": "degenerate"}
    half = _sp_stats.t.ppf(1.0 - (1.0 - confidence) / 2.0, n - 1) * sd / (n ** 0.5)
    return {
        "mean": round(mu, 4),
        "lo": round(mu - half, 4),
        "hi": round(mu + half, 4),
        "n": n,
        "method": "student_t",
    }


# ---------------------------------------------------------------------------
# Effect sizes and multiple-comparison correction
# ---------------------------------------------------------------------------


def effect_label(d: Optional[float]) -> str:
    """Conventional Cohen's-d band for ``d`` (sign-insensitive)."""
    if d is None:
        return "unknown"
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    if abs_d < 0.5:
        return "small"
    if abs_d < 0.8:
        return "medium"
    return "large"


def adjust_pvalues(
    pvals: Sequence[Optional[float]], method: str = "holm", *, rounded: bool = True
) -> List[Optional[float]]:
    """Return multiplicity-adjusted p-values, preserving input order.

    ``holm`` (Holm-Bonferroni, step-down) controls the family-wise error rate
    and is the default: with one baseline and a handful of treatment arms the
    family is small and FWER is the honest target. ``bh``
    (Benjamini-Hochberg, step-up) controls the false discovery rate instead —
    use it when screening many arms and some false positives are tolerable.

    ``None`` entries (a test that could not run) stay ``None`` and are excluded
    from the family size, so an unavailable test never inflates the others.

    Pass **unrounded** p-values. Correcting a rounded p multiplies its
    quantization error by the family size: a true p of 0.016666 in a family of
    three has an exact Holm p of 0.049998 (significant), but
    ``3 * round(0.016666, 4) = 0.0501`` (not significant). The correction math
    runs at full precision and only the returned values are rounded;
    ``rounded=False`` returns them unrounded, for a decision against alpha —
    the rounded 0.049998 displays as 0.05. To decide significance, use
    :func:`correct_family`, which returns both.
    """
    if method not in CORRECTIONS:
        raise ValueError(f"method must be one of {CORRECTIONS}, got {method!r}")

    def finish(p: float) -> Optional[float]:
        return _round_p(p) if rounded else p

    indexed = [(i, p) for i, p in enumerate(pvals) if p is not None]
    out: List[Optional[float]] = [None] * len(pvals)
    m = len(indexed)
    if m == 0:
        return out

    if method == "holm":
        # Step-down: ascending p, multiplier (m - rank), enforce monotonicity.
        indexed.sort(key=lambda t: t[1])
        running = 0.0
        for rank, (idx, p) in enumerate(indexed):
            adj = min(1.0, (m - rank) * p)
            running = max(running, adj)
            out[idx] = finish(running)
    else:
        # Step-up: descending p, multiplier m / rank, enforce monotonicity.
        indexed.sort(key=lambda t: t[1], reverse=True)
        running = 1.0
        for pos, (idx, p) in enumerate(indexed):
            rank = m - pos
            adj = min(1.0, p * m / rank)
            running = min(running, adj)
            out[idx] = finish(running)

    return out


def correct_family(
    pvals: Sequence[Optional[float]], method: str = "holm"
) -> List[Tuple[Optional[float], Optional[bool]]]:
    """Return ``(display p, significant)`` per input, in input order.

    The verdict is ``adjusted < ALPHA`` on the unrounded adjusted p, and the
    display p is that value rounded to 4 decimals. Deciding on the rounded
    value flips the verdict at the boundary (see :func:`adjust_pvalues`).
    Both entries are ``None`` where the input p is ``None``.
    """
    exact = adjust_pvalues(pvals, method=method, rounded=False)
    return [
        (None, None) if adj is None else (_round_p(adj), adj < ALPHA)
        for adj in exact
    ]


# ---------------------------------------------------------------------------
# Paired comparison
# ---------------------------------------------------------------------------


def _round_p(p: Optional[float]) -> Optional[float]:
    """Round a p-value without flattening very small ones to ``0.0``.

    Plain ``round(p, 4)`` turns 3e-8 into 0.0, which reads as an impossible
    exact zero in the saved artifact. Values at or above 1e-4 round to 4
    decimals; smaller ones keep 3 significant digits.
    """
    if p is None:
        return None
    p = float(p)
    if p >= 1e-4:
        return round(p, 4)
    return float(f"{p:.3g}")


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals)


def _sample_sd(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 0.0
    mu = _mean(vals)
    return (sum((x - mu) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5


#: z for a two-sided 95% interval.
_Z95 = 1.959963984540054


def _wilson(p: float, n: int, z: float = _Z95) -> Tuple[float, float]:
    """Wilson interval (95% by default) for a mean ``p`` observed over ``n`` trials.

    Shared by the judge-validity gate and the task track
    (``binary_stats.wilson_interval``).
    """
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(max(0.0, p * (1.0 - p)) / n + z2 / (4 * n * n)) / denom
    # Clamped to contain p: at p = 1.0, centre + half is 0.9999999999999999 for
    # some n, so 10/10 cleared a 1.0 gate while 20/20 straddled it.
    return (max(0.0, min(p, centre - half)), min(1.0, max(p, centre + half)))


def paired_vectors(
    evaluations: Iterable[RecordLike], arm_a: str, arm_b: str
) -> Tuple[List[float], List[float]]:
    """Per-question score vectors for ``arm_a`` and ``arm_b``, complete cases only.

    A question contributes a pair only when *both* arms produced an aggregate,
    which is what makes the downstream test genuinely paired. Order follows
    ``evaluations``.
    """
    a_vals: List[float] = []
    b_vals: List[float] = []
    for ev in records(evaluations, source="evaluations", kind="evaluations"):
        a, b = ev.score(arm_a), ev.score(arm_b)
        if a is not None and b is not None:
            a_vals.append(float(a))
            b_vals.append(float(b))
    return a_vals, b_vals


@dataclass(frozen=True)
class PairedSummary:
    """Both arms of one question set over its complete pairs, and the delta between them.

    Every number is over the questions scored in both arms. The ``unpaired`` ones
    (scored in one arm, or neither) are left out of all of them and counted here,
    so no report can print a context mean over questions its baseline mean lacks.
    The values are unrounded: round only when rendering.
    """

    total: int
    paired: int
    unpaired: int
    with_mean: float
    without_mean: float
    delta_mean: float
    with_min: float
    with_max: float
    without_min: float
    without_max: float
    delta_min: float
    delta_max: float
    improvements: int
    degradations: int
    neutral: int


def paired_summary(evaluations: Iterable[RecordLike]) -> PairedSummary:
    """The arms and their delta over the complete pairs of ``evaluations``.

    The delta is the difference of the two paired means, which is also the mean of the
    per-question deltas (``context - baseline``). Averaging each arm over *every* scored
    question instead gives two means over different questions, whose difference is not
    the delta the page prints beside them. An empty input gives zeros throughout.
    """
    recs = records(evaluations, source="evaluations", kind="evaluations")
    with_s, without_s = paired_vectors(recs, CONTEXT, BASELINE)
    deltas = [w - wo for w, wo in zip(with_s, without_s, strict=True)]
    paired = len(deltas)
    return PairedSummary(
        total=len(recs),
        paired=paired,
        unpaired=len(recs) - paired,
        with_mean=_mean(with_s) if paired else 0.0,
        without_mean=_mean(without_s) if paired else 0.0,
        delta_mean=_mean(deltas) if paired else 0.0,
        with_min=min(with_s, default=0.0),
        with_max=max(with_s, default=0.0),
        without_min=min(without_s, default=0.0),
        without_max=max(without_s, default=0.0),
        delta_min=min(deltas, default=0.0),
        delta_max=max(deltas, default=0.0),
        improvements=sum(1 for d in deltas if d > 0),
        degradations=sum(1 for d in deltas if d < 0),
        neutral=sum(1 for d in deltas if d == 0),
    )


def point_biserial(flags: Sequence[bool], totals: Sequence[float]) -> Dict[str, Any]:
    """Discrimination of one binary item against respondents' total scores.

    Classical item analysis: an item that the strong respondents pass and the weak ones
    fail correlates positively with the total; one that everybody passes, or that the
    weak respondents pass more often, is not measuring what the total measures. Equal to
    the Pearson correlation between ``flags`` (0/1) and ``totals``, computed in the
    point-biserial form so the degenerate cases are explicit rather than a division error.

    Args:
        flags: Per-respondent pass/fail on this item.
        totals: Each respondent's total score, same order and length.

    Returns:
        ``{"r": float | None, "n": int, "n_pass": int, "reason": str | None}``. ``r`` is
        ``None`` when it is undefined: fewer than three respondents, no variance in the
        totals, or an item every respondent passed or failed. ``reason`` names which.
    """
    if len(flags) != len(totals):
        raise ValueError(
            f"point_biserial needs one total per flag, got {len(flags)} flags and "
            f"{len(totals)} totals; truncating would return a number for a mismatch"
        )
    pairs = [(bool(f), float(t)) for f, t in zip(flags, totals, strict=True)]
    n = len(pairs)
    n_pass = sum(1 for flag, _ in pairs if flag)
    if n < 3:
        return {"r": None, "n": n, "n_pass": n_pass, "reason": "fewer than 3 respondents"}
    if n_pass in (0, n):
        return {"r": None, "n": n, "n_pass": n_pass,
                "reason": "no variance in the item: every respondent scored it the same"}
    all_totals = [t for _, t in pairs]
    sd = _sample_sd(all_totals)
    if _is_degenerate(_mean(all_totals), sd):
        return {"r": None, "n": n, "n_pass": n_pass,
                "reason": "no variance in the totals: nothing for the item to discriminate"}
    mean_pass = _mean([t for flag, t in pairs if flag])
    mean_fail = _mean([t for flag, t in pairs if not flag])
    proportion = n_pass / n
    # Population sd, which is the point-biserial convention; _sample_sd is n-1.
    sd_pop = sd * ((n - 1) / n) ** 0.5
    r = (mean_pass - mean_fail) / sd_pop * (proportion * (1 - proportion)) ** 0.5
    return {"r": round(r, 4), "n": n, "n_pass": n_pass, "reason": None}


def _one_sample_pvalues(deltas: Sequence[float]) -> Dict[str, Any]:
    """Two-sided p-values for ``mean(deltas) != 0`` (t-test and Wilcoxon).

    p-values come back at **full precision**; rounding happens in
    :func:`_finalize`, after any multiplicity correction. Correcting an
    already-rounded p multiplies its quantization error by the family size: a
    true p of 0.016666 in a family of three gives an exact Holm p of 0.049998
    (significant), but 3 x round(0.016666, 4) = 0.0501 (not significant).

    Returns ``None`` for both p-values when the deltas have zero variance: the
    t statistic is then infinite and SciPy reports ``p == 0``, which would read
    as overwhelming significance on what is really a degenerate sample (often a
    stub judge returning a constant). The CI still describes it.

    ``n_wilcoxon`` is the number of pairs the signed-rank test actually used.
    """
    res: Dict[str, Any] = {"p_ttest": None, "p_wilcoxon": None, "n_wilcoxon": 0}
    if not HAS_SCIPY or len(deltas) < MIN_PAIRS_FOR_TEST:
        return res
    if _is_degenerate(_mean(deltas), _sample_sd(deltas)):
        return res
    try:
        _, p_t = _sp_stats.ttest_1samp(list(deltas), 0.0)
        res["p_ttest"] = float(p_t)
    except Exception:  # pragma: no cover - degenerate input
        pass
    try:
        # Wilcoxon is undefined when every delta is zero.
        if any(d != 0 for d in deltas):
            # zero_method="zsplit" keeps tied pairs in the sample size and
            # splits their rank sum. SciPy's default ("wilcox") *discards*
            # them, which on judged scores — where ties are routine — silently
            # runs the test on a fraction of the pairs while ``n`` reports the
            # full count. Measured on 20 pairs with 15 ties: 0.0253 discarding
            # versus 0.0764 splitting, i.e. the verdict flips.
            _, p_w = _sp_stats.wilcoxon(
                list(deltas), zero_method="zsplit", alternative="two-sided"
            )
            res["p_wilcoxon"] = float(p_w)
            res["n_wilcoxon"] = len(deltas)
    except Exception:  # pragma: no cover - degenerate input
        pass
    return res


def _describe_deltas(
    deltas: Sequence[float], *, confidence: float, seed: int
) -> Dict[str, Any]:
    """Effect size, CI, and hypothesis tests for a delta vector.

    p-values are left unrounded for the caller to correct and then
    :func:`_finalize`.
    """
    d_mean = _mean(deltas)
    d_sd = _sample_sd(deltas)
    degenerate = _is_degenerate(d_mean, d_sd)
    if not degenerate:
        cohens_d = round(d_mean / d_sd, 3)
    elif d_mean == 0:
        # Every pair identical: the limit of 0/0 here is a genuine null, and
        # "0.0 (negligible)" says more than "unknown" would.
        cohens_d = 0.0
    else:
        # d_z is *unbounded* when the deltas are constant and non-zero, not
        # zero: reporting 0.0 would label a flawless, perfectly consistent
        # shift "negligible". None renders as "unknown" — what we actually know.
        cohens_d = None
    ci = mean_ci(deltas, confidence=confidence, seed=seed)

    result: Dict[str, Any] = {
        "n": len(deltas),
        "delta_mean": round(d_mean, 2),
        "delta_std": round(d_sd, 2),
        "cohens_d": cohens_d,
        "effect": effect_label(cohens_d),
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "ci_confidence": confidence,
        "ci_method": ci.get("method"),
    }
    result.update(_one_sample_pvalues(deltas))

    p_t, p_w = result["p_ttest"], result["p_wilcoxon"]
    # None, not False, when no test could run: "we could not tell" is not "we
    # showed no difference". A constant +20 shift would otherwise render
    # identically to a true null.
    result["significant"] = (
        None if p_t is None
        else bool(p_t < ALPHA and (p_w is None or p_w < ALPHA))
    )
    # Filled in by arm_significance() once the family size is known.
    result["p_adjusted"] = None
    result["significant_adjusted"] = None
    return result


def _finalize(result: Dict[str, Any]) -> Dict[str, Any]:
    """Round every p-value in ``result`` in place, once, at the end."""
    for key in ("p_ttest", "p_wilcoxon", "p_adjusted"):
        if key in result:
            result[key] = _round_p(result[key])
    return result


def paired_test(
    treatment: Sequence[float],
    control: Sequence[float],
    *,
    confidence: float = 0.95,
    seed: int = 0,
    rounded: bool = True,
) -> Optional[Dict[str, Any]]:
    """Paired comparison of ``treatment`` against ``control``.

    Returns ``None`` when the vectors are empty, mismatched in length, or hold
    fewer than two pairs. Effect size and CI are always computed;
    ``p_ttest``/``p_wilcoxon`` are ``None`` without SciPy or below
    :data:`MIN_PAIRS_FOR_TEST` pairs. ``rounded=False`` leaves the p-values
    unrounded for a caller that corrects them first, then :func:`_finalize`.
    """
    if len(treatment) != len(control) or len(treatment) < 2:
        return None
    deltas = [t - c for t, c in zip(treatment, control, strict=True)]
    result = _describe_deltas(deltas, confidence=confidence, seed=seed)
    return _finalize(result) if rounded else result


def arm_significance(
    evaluations: Iterable[RecordLike],
    baseline_arm: str,
    arms: Optional[Sequence[str]] = None,
    *,
    correction: str = "holm",
    confidence: float = 0.95,
    seed: int = 0,
) -> Dict[str, Dict[str, Any]]:
    """Test every non-baseline arm against one *shared* baseline.

    Pairing happens per question against the same baseline sample, so the arms
    are mutually comparable — the deltas differ only by treatment, not by a
    re-sampled control. p-values are then corrected across the arms tested in
    this call (see :func:`adjust_pvalues`).
    """
    evaluations = records(evaluations, source="evaluations", kind="evaluations")
    if arms is None:
        seen: List[str] = []
        for ev in evaluations:
            for name in ev.conditions:
                if name not in seen:
                    seen.append(name)
        arms = seen

    targets = [a for a in arms if a != baseline_arm]
    out: Dict[str, Dict[str, Any]] = {}
    for arm in targets:
        treat, base = paired_vectors(evaluations, arm, baseline_arm)
        # _describe_deltas, not paired_test: the correction below must see the
        # raw p-values, and paired_test rounds on the way out.
        if len(treat) == len(base) and len(treat) >= 2:
            out[arm] = _describe_deltas(
                [t - c for t, c in zip(treat, base, strict=True)],
                confidence=confidence,
                seed=seed,
            )

    ordered = [a for a in targets if a in out]
    _apply_correction(
        [out[a] for a in ordered], correction=correction, label="arms"
    )
    for arm in ordered:
        _finalize(out[arm])
    return out


def _apply_correction(
    results: Sequence[Dict[str, Any]], *, correction: str, label: str
) -> None:
    """Correct ``p_ttest`` across one family of results, in place.

    ``results`` must still hold unrounded p-values. ``label`` names the family
    so a reader can tell which hypotheses were corrected together — arms and
    interaction contrasts are separate families, and pooling them would inflate
    the multiplier on both.
    """
    decided = correct_family([r["p_ttest"] for r in results], method=correction)
    tested = sum(1 for r in results if r["p_ttest"] is not None)
    for res, (adj, adj_significant) in zip(results, decided, strict=True):
        res["p_adjusted"] = adj
        res["correction"] = correction
        res["correction_family"] = label
        # Members of the family, and how many yielded a testable p-value (the
        # effective multiplier — see adjust_pvalues).
        res["family_size"] = len(results)
        res["family_tested"] = tested
        p_w = res["p_wilcoxon"]
        # None, not False, when there was no p to adjust: see _describe_deltas.
        res["significant_adjusted"] = (
            None if adj_significant is None
            else bool(adj_significant and (p_w is None or p_w < ALPHA))
        )
