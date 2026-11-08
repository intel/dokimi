# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Small statistics helpers for evaluation rigor.

Bootstrap CIs, effect sizes, and multiple-comparison correction need no
third-party packages. The paired tests (:func:`paired_test`,
:func:`arm_significance`, and the interaction contrasts of
:mod:`dokimi.eval.interactions`) are one two-sided sign-flip test on the
per-question deltas, and their 95% interval inverts it
(:mod:`dokimi.eval.sign_flip`, NumPy only), so the interval excludes 0 exactly
when the raw p is below :data:`ALPHA`. Only :func:`mean_ci` reads SciPy.

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
paired_delta(evaluations, baseline_arm, arm_name)
    Mean of ``arm - baseline`` over the complete pairs, rounded to one place (the
    arm table's delta column); ``None`` with no pairs.
paired_summary(evaluations)
    Both arms and their delta over the complete pairs, with the unpaired count.
    Called by the eval report, the report generator and the pipeline summary; the
    dashboard and the model-compare run summary average both arms over the same pairs.
mean_ci(values, ...)
    Student-t CI for the mean; bootstrap fallback. For the checklist and
    cross-judge summaries, not the paired tests.
point_biserial(flags, totals)
    Item-analysis discrimination: correlation of one binary item against the
    respondents' total scores.
paired_test(treatment, control, ...)
    Sign-flip p + its inverted 95% interval + Cohen's d_z on the deltas.
arm_significance(evaluations, baseline_arm, ...)
    Every non-baseline arm tested against one *shared* baseline, with
    family-wise correction of the sign-flip p across the arms.

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

from dokimi.eval.sign_flip import (ALPHA, EPS, METHOD, _rounded, min_sign_flip_p, sign_flip_interval,
                                   sign_flip_p, within)
from dokimi.records import BASELINE, CONTEXT, RecordLike, records

try:
    from scipy import stats as _sp_stats

    HAS_SCIPY = True
except ImportError:  # pragma: no cover - environment dependent
    _sp_stats = None
    HAS_SCIPY = False

#: Accepted multiple-comparison corrections (see :func:`adjust_pvalues`).
CORRECTIONS = ("holm", "bh")

#: Relative spread (scaled by the mean) below which d_z is undefined: a float constant shift has sd ~ 3e-15,
#: not 0, and d_z ~ 2.8e13 ("large"). The sign-flip p needs no such guard.
ZERO_VARIANCE_REL_TOL = 1e-9


def _is_degenerate(mean: float, sd: float) -> bool:
    """True when ``sd`` is zero or indistinguishable from it at this scale."""
    return sd <= ZERO_VARIANCE_REL_TOL * max(1.0, abs(mean))


def _rnd(x: float, ndigits: Optional[int]) -> float:
    """``round(x, ndigits)``, or ``x`` itself when ``ndigits`` is None."""
    return x if ndigits is None else round(x, ndigits)


def bootstrap_ci(
    values, confidence: float = 0.95, n_resamples: int = 10_000, seed: int = 0,
    ndigits: Optional[int] = 4,
) -> dict:
    """Percentile bootstrap confidence interval for the mean of ``values``.

    Returns ``{"mean", "lo", "hi", "n"}``. Deterministic for a given ``seed``,
    so it is safe to use in tests and reproducible runs.

    This is the eval-side bootstrap. Its seed is an integer fed to
    ``random.Random(seed)``, the scheme the McNemar and grounding intervals
    already used, and the one ``--seed`` means on the CLI. Novelty's atom-clustered ``clustered_ci``
    (``novelty/controls.py``) is a separate resampler with its own string seed;
    this module does not change it. Callers pass ``n_resamples`` when they need
    a count other than the default.

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
        return {"mean": _rnd(vals[0], ndigits), "lo": _rnd(vals[0], ndigits), "hi": _rnd(vals[0], ndigits), "n": 1}

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
        "mean": _rnd(sum(vals) / n, ndigits),
        "lo": _rnd(means[lo_idx], ndigits),
        "hi": _rnd(means[hi_idx], ndigits),
        "n": n,
        "method": "percentile_bootstrap",
    }


def mean_ci(values, confidence: float = 0.95, seed: int = 0, ndigits: Optional[int] = 4) -> dict:
    """Confidence interval for the mean: Student-t when SciPy is available.

    The t interval is better calibrated than the percentile bootstrap at small
    n, and excludes zero exactly when a one-sample t test rejects. The paired
    tests do not use it: their interval inverts their sign-flip test
    (:func:`paired_test`); this one serves the checklist and cross-judge
    summaries.

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
        return bootstrap_ci(vals, confidence=confidence, seed=seed, ndigits=ndigits)

    mu = _mean(vals)
    sd = _sample_sd(vals)
    if _is_degenerate(mu, sd):
        # Every value identical: the interval is the point itself, and a
        # bootstrap would report the same thing after 10k pointless resamples.
        return {"mean": _rnd(mu, ndigits), "lo": _rnd(mu, ndigits), "hi": _rnd(mu, ndigits),
                "n": n, "method": "degenerate"}
    half = _sp_stats.t.ppf(1.0 - (1.0 - confidence) / 2.0, n - 1) * sd / (n ** 0.5)
    return {
        "mean": _rnd(mu, ndigits),
        "lo": _rnd(mu - half, ndigits),
        "hi": _rnd(mu + half, ndigits),
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


def paired_delta(evaluations: Iterable[Any], baseline_arm: str, arm_name: str) -> Optional[float]:
    """The mean of ``arm - baseline`` over the questions that both arms scored; ``None`` if none.

    A question one arm errored on (or left unscored) is not in the delta at all: the
    difference of two unpaired means would compare different questions.
    """
    arm_vals, base_vals = paired_vectors(evaluations, arm_name, baseline_arm)
    diffs = [a - b for a, b in zip(arm_vals, base_vals, strict=True)]
    return round(sum(diffs) / len(diffs), 1) if diffs else None


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


def _cohens_d(d_mean: float, d_sd: float) -> Optional[float]:
    """Cohen's d_z of the deltas (sample SD), descriptive: no verdict reads it."""
    if not _is_degenerate(d_mean, d_sd):
        return round(d_mean / d_sd, 3)
    # Every pair identical: the limit of 0/0 here is a genuine null, and "0.0 (negligible)" says more than
    # "unknown"; a constant non-zero shift has an unbounded d_z, which None renders as "unknown".
    return 0.0 if d_mean == 0 else None


def _describe_deltas(
    deltas: Sequence[float], *, confidence: float, seed: int
) -> Dict[str, Any]:
    """Effect size, the sign-flip p, and the interval that inverts it, for a delta vector.

    ``p`` is the two-sided sign-flip p over the deltas not within ``EPS`` of 0
    (:func:`dokimi.eval.sign_flip.sign_flip_p`, exact up to 16 of them, Monte
    Carlo from ``seed`` above), left unrounded for the caller to correct and
    then :func:`_finalize`. Correcting an already-rounded p multiplies its
    quantization error by the family size: a true p of 0.016666 in a family of
    three gives an exact Holm p of 0.049998 (significant), but
    3 x round(0.016666, 4) = 0.0501 (not significant).

    ``ci_lo``/``ci_hi`` are every shift the same test does not reject at
    :data:`ALPHA`, uncorrected (``ci_method`` ``sign_flip_inversion``), placed
    on 0 by that same ``p``: they exclude 0 exactly when ``p < ALPHA``. An end
    is ``None`` where it is open, as both are at 5 or fewer pairs. ``exact``
    holds the unrounded ends; the displayed ones are rounded to 4 places, never
    onto or across 0, and ``delta_mean`` (2 places) is held inside them.

    ``significant`` is ``None`` ("could not tell", not "no") when there is no
    p, every delta tied, or when the floor ``2 / 2**k`` over the
    ``n_changed`` = k changed pairs is not below :data:`ALPHA`: at 5 or fewer
    changed pairs no data whatsoever could give a positive verdict, however many
    tied pairs there are. Otherwise it is ``p < ALPHA``.

    ``confidence`` must be 0.95: the interval inverts the test at
    :data:`ALPHA`, so another level would no longer agree with the verdict.
    """
    if not math.isclose(confidence, 1.0 - ALPHA):
        raise ValueError(f"confidence must be {1.0 - ALPHA}: the interval inverts the test at alpha {ALPHA}")
    d = [float(x) for x in deltas]
    d_mean = _mean(d)
    d_sd = _sample_sd(d)
    cohens_d = _cohens_d(d_mean, d_sd)
    p = sign_flip_p(d, seed=seed, exact=True)
    lo, hi = sign_flip_interval(d, p, seed=seed, rounded=False)
    shown_lo, shown_hi = _rounded(lo), _rounded(hi)
    n_changed = sum(1 for x in d if abs(x) > EPS)

    return {
        "n": len(d),
        "delta_mean": within(round(d_mean, 2) + 0.0, shown_lo, shown_hi),
        "delta_std": round(d_sd, 2),
        "cohens_d": cohens_d,
        "effect": effect_label(cohens_d),
        "ci_lo": shown_lo,
        "ci_hi": shown_hi,
        "ci_confidence": confidence,
        "ci_method": METHOD,
        # The fields above are rounded for display; a consumer that re-derives a verdict
        # or publishes the effect reads these. p_adjusted is set with the family correction.
        "exact": {"delta_mean": d_mean, "ci_lo": lo, "ci_hi": hi},
        "p": p,
        "n_changed": n_changed,
        "significant": None if p is None or min_sign_flip_p(n_changed) >= ALPHA else p < ALPHA,
        # Filled in by _apply_correction() once the family size is known.
        "p_adjusted": None,
        "significant_adjusted": None,
    }


def _finalize(result: Dict[str, Any]) -> Dict[str, Any]:
    """Round ``p`` and ``p_adjusted`` in ``result`` in place, once, at the end."""
    for key in ("p", "p_adjusted"):
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
    """Paired comparison of ``treatment`` against ``control``: :func:`_describe_deltas` of their differences.

    Returns ``None`` when the vectors are empty, mismatched in length, or hold
    fewer than two pairs. From two pairs on the sign-flip test runs and ``p`` is
    reported; ``significant`` is ``None`` where it has no p or the floor rule
    leaves no verdict. No multiplicity correction: ``p_adjusted`` and
    ``significant_adjusted`` stay ``None``. ``rounded=False`` leaves ``p``
    unrounded for a caller that corrects it first, then :func:`_finalize`.
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
    re-sampled control. Each arm with 2+ pairs gets :func:`_describe_deltas`;
    its sign-flip ``p`` is then corrected across the arms tested in this call
    (:func:`_apply_correction`, :func:`adjust_pvalues`). The interval stays
    uncorrected.
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


def set_exact_p_adjusted(results: Sequence[Dict[str, Any]], *, method: str) -> None:
    """Set ``exact.p_adjusted`` to the family correction of ``p`` before rounding."""
    exact = adjust_pvalues([r["p"] for r in results], method=method, rounded=False)
    for res, p in zip(results, exact, strict=True):
        if "exact" in res:
            res["exact"]["p_adjusted"] = p


def _apply_correction(
    results: Sequence[Dict[str, Any]], *, correction: str, label: str
) -> None:
    """Correct the sign-flip ``p`` across one family of results, in place.

    ``results`` must still hold unrounded p-values. ``label`` names the family
    so a reader can tell which hypotheses were corrected together — arms and
    interaction contrasts are separate families, and pooling them would inflate
    the multiplier on both. Every result with a p is in the family, one under
    the floor rule included (as in ``repo compare``), and ``family_tested``
    counts them. ``significant_adjusted`` is ``None`` wherever ``significant``
    is (no p, or the floor rule), else the corrected verdict.
    """
    decided = correct_family([r["p"] for r in results], method=correction)
    set_exact_p_adjusted(results, method=correction)
    tested = sum(1 for r in results if r["p"] is not None)
    for res, (adj, adj_significant) in zip(results, decided, strict=True):
        res["p_adjusted"] = adj
        res["correction"] = correction
        res["correction_family"] = label
        # Members of the family, and how many yielded a testable p-value (the
        # effective multiplier — see adjust_pvalues).
        res["family_size"] = len(results)
        res["family_tested"] = tested
        res["significant_adjusted"] = None if res["significant"] is None else adj_significant
