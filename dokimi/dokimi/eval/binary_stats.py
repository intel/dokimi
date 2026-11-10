# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Paired statistics for **binary** outcomes (the execution-verified track).

The judged Q&A track produces 0–100 scores, so
:mod:`dokimi.eval.stats` tests them with a paired sign-flip test on the
deltas. The executable-task track produces a single bit per cell — the task's
own test suite exited 0 or it did not — and a t-test on 0/1 data is the wrong
instrument: with 20 tasks the deltas are ``{-1, 0, +1}``, badly non-normal, and
the interesting information is entirely in the *disagreements*.

McNemar's test is the paired test for that shape. It conditions on the
discordant cells (tasks the treatment fixed, tasks it broke) and ignores the
tasks both arms passed or both failed, which carry no evidence about a
difference. Two consequences worth knowing before reading a p-value here:

* Adding tasks that both arms already solve does not add power. Ten new easy
  tasks that everyone passes leave the test statistic untouched.
* With few discordant tasks the test simply cannot reach significance. Four
  gains and zero losses gives *p* = 0.125 — suggestive, not significant. That is
  the honest answer, not a defect to work around.

The test assumes the pairs are **independent**, which is worth stating because
the caller can easily break it: running every task ``repeats`` times and pairing
per ``(task, repeat)`` cell yields ``n_tasks * repeats`` pairs but only
``n_tasks`` independent units. Repeats of one task share its difficulty, so the
effective sample size is smaller than ``n`` and the p-value is anti-conservative
— too small — by an amount that depends on how strongly repeats correlate.
:func:`mcnemar_test` therefore reports ``n_units`` alongside ``n`` and sets
``pseudo_replicated`` when they differ, so a reader is told rather than left to
notice. Correcting for the clustering (a GEE or cluster-bootstrap McNemar) is
future work; disclosing it is not.

The exact binomial form is used rather than the chi-square approximation
(with or without Yates' continuity correction), because on a 20-task suite the
discordant count is routinely under 10, where the approximation is unreliable.
Exact also means no SciPy: the null is ``Binomial(b + c, 0.5)``, whose two-sided
tail is computable with :func:`math.comb`.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence

# _round_p and _wilson are private to the package, not to this module: both
# tracks must round p-values and bound rates the same way, or the two reports
# disagree on what "0.0" means.
from dokimi.eval.stats import ALPHA, _round_p, _wilson, bootstrap_ci

#: Fewest paired tasks before a result is returned at all. Unlike the judged
#: track this is not a power threshold — McNemar's exact p is honest at any n —
#: only a guard against calling a single task a comparison.
MIN_PAIRS_FOR_TEST = 2

#: Confidence of the per-arm pass-rate interval, here and in the report header.
PASS_RATE_CI_CONFIDENCE = 0.95


def exact_binomial_two_sided(successes: int, trials: int) -> Optional[float]:
    """Two-sided exact p for ``successes`` out of ``trials`` under p = 0.5.

    ``None`` when ``trials`` is 0: with no discordant pair there is no evidence
    either way, which is different from evidence of no difference.
    """
    if trials < 0 or not 0 <= successes <= trials:
        raise ValueError(f"successes must be in [0, {trials}], got {successes}")
    if trials == 0:
        return None
    # The null distribution is symmetric, so the two-sided tail is exactly
    # twice the smaller one — no doubling ambiguity as in the general case.
    k = min(successes, trials - successes)
    tail = sum(math.comb(trials, i) for i in range(k + 1)) / (2 ** trials)
    return min(1.0, 2.0 * tail)


def wilson_interval(
    successes: int, trials: int, confidence: float = PASS_RATE_CI_CONFIDENCE,
) -> tuple[Optional[float], Optional[float]]:
    """Wilson score interval for one arm's pass rate, ``(None, None)`` at n = 0.

    Wilson rather than the normal approximation because executable suites are
    small and pass rates sit near 0 or 1, where ``p ± z·sqrt(p(1-p)/n)``
    collapses to a zero-width interval or leaves [0, 1]. It is **not** what
    SkillsBench reports, so the two are not directly comparable: their paper
    uses the Wald interval ``1.96·sqrt(p(1-p)/n)`` (v4, App. G) after a
    percentile bootstrap in v1, and their leaderboard a task-level
    ``1.96·stdev(per-task means)/sqrt(87)``, which accounts for repeats. Like
    McNemar this one assumes independent trials: with repeats of one task it
    is too narrow.
    """
    if trials < 0 or not 0 <= successes <= trials:
        raise ValueError(f"successes must be in [0, {trials}], got {successes}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if trials == 0:
        return None, None
    lo, hi = _wilson(successes / trials, trials, NormalDist().inv_cdf(0.5 + confidence / 2))
    return round(lo, 4), round(hi, 4)


def normalized_gain(arm_rate: Optional[float], base_rate: Optional[float]) -> Optional[float]:
    """Hake's normalized gain ``(arm - base) / (1 - base)``, as SkillsBench uses it.

    The share of the baseline's headroom gained: a +10 pp lift means more on
    an 85% baseline than on a 30% one. It equals SkillsBench's
    per-configuration value only when every cell scored and rewards are
    binary: they average per-task mean *rewards* and count a missing cell as
    0, where the caller here passes cell-level binary pass rates with shared
    errored cells dropped. Hake's form is **unbounded below** (``0.5`` vs
    ``0.95`` is ``-9.0``); read regressions from :func:`normalized_change`.
    ``None`` when either rate is missing or the baseline passes everything.
    """
    if arm_rate is None or base_rate is None or base_rate >= 1.0:
        return None
    return round((arm_rate - base_rate) / (1.0 - base_rate), 4)


def normalized_change(arm_rate: Optional[float], base_rate: Optional[float]) -> Optional[float]:
    """Marx & Cummings (2007) piecewise formula applied to aggregate pass rates.

    M&C compute ``c`` per student and average; here it is applied once to the
    two arms' pass rates, bounded to [-1, 1]. Equal to :func:`normalized_gain`
    for a gain. A loss is normalised by what the baseline had to lose,
    ``(arm - base) / base``, so ``-1`` means every baseline pass was lost.
    Other equal rates give ``0``. The two halves are on different scales — at
    a 0.8 baseline +0.2 is +4 pp but -0.2 is -16 pp — so compare signs and
    bounds, not magnitudes across signs. ``None`` when either rate is missing,
    or when both are 0 or both 1: M&C drop that case rather than score it 0.
    """
    if arm_rate is None or base_rate is None:
        return None
    if arm_rate == base_rate and base_rate in (0.0, 1.0):
        return None
    delta = arm_rate - base_rate
    if delta == 0:
        return 0.0
    room = (1.0 - base_rate) if delta > 0 else base_rate
    return round(delta / room, 4)


def mcnemar_test(
    treatment: Sequence[bool],
    control: Sequence[bool],
    *,
    confidence: float = 0.95,
    seed: int = 0,
    units: Optional[Sequence[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """McNemar's exact test on paired pass/fail outcomes.

    ``treatment[i]`` and ``control[i]`` must be the same task (or task-repeat)
    solved under two harnesses. Returns ``None`` when the vectors differ in
    length or hold fewer than :data:`MIN_PAIRS_FOR_TEST` pairs.

    ``units[i]`` names the independent unit pair *i* belongs to — the task, when
    pairs are ``(task, repeat)`` cells. Supplying it lets the result disclose
    that repeats are not independent pairs (see the module docstring); omitting
    it asserts every pair is its own unit.

    Keys:

    ``n_gained`` / ``n_lost``
        Tasks the treatment fixed / broke — the discordant cells, and the only
        ones the p-value depends on.
    ``n_both_passed`` / ``n_both_failed``
        The concordant cells. Reported because they say whether a null result
        means "no effect" or "the suite has no headroom left".
    ``paired_pass_rate_delta``
        ``(n_gained - n_lost) / n``. Named apart from the ``pass_rate_delta``
        the suite summary reports beside it: that one subtracts two marginal
        pass rates over every row each harness ran, this one is a pairwise mean
        over the *shared* cells only. The two disagree whenever any cell is
        excluded, and sharing a name invited reading one number as the other.
    ``ci_lo`` / ``ci_hi``
        Percentile bootstrap interval for ``paired_pass_rate_delta``, resampling
        the per-task ``{-1, 0, +1}`` outcomes.
    ``p_exact``
        Two-sided exact binomial p, or ``None`` with no discordant pair.
    ``significant``
        ``p_exact < 0.05``; ``None`` when no test could run, never ``False`` on
        absent evidence.
    ``n_units`` / ``pseudo_replicated``
        Independent units behind the ``n`` pairs, and whether they are fewer.
        ``True`` means ``p_exact`` is anti-conservative — read it as a screening
        signal, not a claim at α.
    """
    if len(treatment) != len(control) or len(treatment) < MIN_PAIRS_FOR_TEST:
        return None
    if units is not None and len(units) != len(treatment):
        raise ValueError(
            f"units must be one per pair: got {len(units)} for {len(treatment)} pairs"
        )

    gained = lost = both_passed = both_failed = 0
    deltas: List[float] = []
    for t, c in zip(treatment, control, strict=True):
        t, c = bool(t), bool(c)
        if t and not c:
            gained += 1
            deltas.append(1.0)
        elif c and not t:
            lost += 1
            deltas.append(-1.0)
        else:
            both_passed += 1 if t else 0
            both_failed += 0 if t else 1
            deltas.append(0.0)

    n = len(deltas)
    n_units = len(set(units)) if units is not None else n
    p_exact = _round_p(exact_binomial_two_sided(gained, gained + lost))
    ci = bootstrap_ci(deltas, confidence=confidence, seed=seed)
    return {
        "test": "mcnemar_exact",
        "n": n,
        "n_units": n_units,
        "pseudo_replicated": n_units < n,
        "n_gained": gained,
        "n_lost": lost,
        "n_both_passed": both_passed,
        "n_both_failed": both_failed,
        "n_discordant": gained + lost,
        "paired_pass_rate_delta": round((gained - lost) / n, 4),
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "ci_confidence": confidence,
        "p_exact": p_exact,
        "significant": None if p_exact is None else p_exact < ALPHA,
        # Filled in by the caller when several harnesses form one family.
        "p_adjusted": None,
        "significant_adjusted": None,
    }
