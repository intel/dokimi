# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The paired sign-flip test and the 95% interval that inverts it, shared by ``repo compare`` and the arms track.

:func:`sign_flip_p` is the two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null,
over the deltas not within :data:`EPS` of 0: exact up to :data:`EXACT_MAX` of them, Monte Carlo above.
:func:`min_sign_flip_p` is the floor it cannot go below over k changed deltas, ``2 / 2**k`` exact.

``p(theta)`` is the two-sided sign-flip p of the shifted deltas ``d - theta`` over every delta, as
:func:`sign_flip_p` computes it on them: a shifted delta within :data:`EPS` of 0 is a tie, so a delta tied
at 0 counts again once ``theta`` moves off 0. Exact, a sign pattern counts when its sum falls short of the observed
one by at most :data:`EPS`, the observed sum taken as the least of its exactly rounded sum and the product's sums for
the observed signs and their mirror: from deltas of about 1e6 on the two round apart by more than :data:`EPS`, and the
observed pattern would not count itself. The observed sum and the mean the bisection starts from are
:func:`math.fsum`'s, not ``sum``'s, which is compensated from Python 3.12 on and adds left to right before: so the
ends are the same on every Python.

The interval is every ``theta`` with ``p(theta)`` at least :data:`ALPHA`, uncorrected; each end is bisected between the mean (where p is 1) and a far bound past
every delta and 0 (1 past the extreme, twice it from 1 on), where the shifted deltas share one sign and p is at its floor, ``2 / 2**n`` over n deltas exact.
So an end is open (``None``) at 5 or fewer deltas, and both are bounded from 6 on, whatever the ties.

Whether 0 is inside is the caller's own p's call (the caller passes it): 0 is inside exactly when that p is at least
ALPHA, so the interval excludes 0 exactly when p < ALPHA. Exact up to :data:`EXACT_MAX` non-zero shifted deltas,
with one cached ``2**k x k`` sign matrix per k and one matrix-vector product per theta; Monte Carlo above, with one
:data:`N_RESAMPLES` x n sign matrix drawn once from the seed and reused at every theta (common random numbers), so the
ends are the same from run to run. Its columns of the changed deltas are the draws :func:`sign_flip_p`
makes for that seed, so the Monte Carlo ``p(0)`` is the printed p up to the rounding :class:`ShiftTest` names.
``p(theta)`` is a step function and the ends are its step points, within :data:`TOL`: there is no mid-p, which would
no longer agree with the exact p a row prints.

This module imports nothing of :mod:`dokimi.eval.stats`, which imports it.
"""

from __future__ import annotations

import functools
import itertools
import math
import random

import numpy as np

#: The level p is read at (two-sided) and the interval is inverted at.
ALPHA = 0.05
#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties.
EPS = 1e-9
#: How close a bisected end comes to the step of ``p(theta)`` it stands for: far below the 3 printed decimals.
TOL = 1e-7
#: The interval's method name: ``delta.method`` of a ``repo compare`` row, ``ci_method`` of an arms result.
METHOD = "sign_flip_inversion"


def min_sign_flip_p(changed: int) -> float:
    """The smallest p :func:`sign_flip_p` can give over ``changed`` non-zero deltas, every sign the same: only that
    pattern and its mirror reach the observed ``|sum|``, ``2 / 2**changed`` exact, ``1 / (N + 1)`` Monte Carlo."""
    return min(1.0, 2 / 2 ** changed) if changed <= EXACT_MAX else 1 / (N_RESAMPLES + 1)


def sign_flip_p(deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES, *,
                exact: bool = False) -> float | None:
    """Two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null.

    ``None`` when every delta is a tie: there is no evidence either way, and 1.0 would read as a measured null.
    Rounded to 6 places as printed, unless ``exact``.
    """
    d = [x for x in deltas if abs(x) > EPS]
    if not d:
        return None
    observed = abs(sum(d)) - EPS
    if len(d) <= EXACT_MAX:
        hits = sum(abs(sum(s * x for s, x in zip(signs, d, strict=True))) >= observed
                   for signs in itertools.product((1, -1), repeat=len(d)))
        p = hits / 2 ** len(d)
    else:
        rng = random.Random(seed)
        hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= observed for _ in range(n_resamples))
        p = (hits + 1) / (n_resamples + 1)
    return p if exact else round(p, 6)


@functools.cache
def _patterns(k: int) -> np.ndarray:
    """Every sign pattern of k deltas, one per row: ``2**k x k``, as floats for the product."""
    bits = (np.arange(2 ** k)[:, None] >> np.arange(k)) & 1
    out = 1.0 - 2.0 * bits
    out.flags.writeable = False
    return out


@functools.lru_cache(maxsize=2)
def _draws(seed: int, changed: int, n: int, n_resamples: int) -> np.ndarray:
    """``n_resamples x n`` random signs: the first ``changed`` columns are the draws of
    :func:`sign_flip_p`, resample by resample; the other columns continue the same stream."""
    key = random.Random(seed).getstate()[1]
    stream = np.random.RandomState()  # random.Random's Mersenne Twister, its state copied: the same doubles
    stream.set_state(("MT19937", np.array(key[:624], dtype=np.uint32), int(key[624]), 0, 0.0))
    first = stream.random_sample((n_resamples, changed))
    out = np.where(np.hstack([first, stream.random_sample((n_resamples, n - changed))]) < 0.5, 1.0, -1.0)
    out.flags.writeable = False
    return out


class ShiftTest:
    """The sign-flip p of ``deltas - theta`` over every delta at any ``theta``, unrounded; at 0 it is
    :func:`sign_flip_p` of ``deltas``, Monte Carlo draws included, up to rounding. From deltas of about 1e6
    on, a sign pattern whose sum ties the observed one in exact arithmetic (magnitudes that repeat, such as 1e9 + 0.1)
    can round to one side of it in the product and to the other in ``sum()``, so ``p(0)`` can differ there: the
    row's own p, not ``p(0)``, places 0 (:func:`sign_flip_interval`)."""

    def __init__(self, deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES) -> None:
        changed = [x for x in deltas if abs(x) > EPS]
        self.d = np.array(changed + [x for x in deltas if abs(x) <= EPS], dtype=float)  # sign_flip_p's order first
        self.changed, self.seed, self.n_resamples = len(changed), seed, n_resamples

    def p(self, theta: float) -> float | None:
        """``None`` when every shifted delta ties, as :func:`sign_flip_p` has no p then."""
        x = self.d - theta
        x[np.abs(x) <= EPS] = 0.0
        moved = x[x != 0.0]
        if not moved.size:
            return None
        observed = abs(math.fsum(moved.tolist()))  # exactly rounded: sum() rounds differently before 3.12
        if moved.size <= EXACT_MAX:
            sums = np.abs(_patterns(moved.size) @ moved)  # rows 0 and -1: the observed signs and their mirror
            least = min(observed, float(sums[0]), float(sums[-1]))  # the product rounds them otherwise from 1e6 on
            return int(np.count_nonzero(sums >= least - EPS)) / 2 ** moved.size
        signs = _draws(self.seed, self.changed, self.d.size, self.n_resamples)
        return (int(np.count_nonzero(np.abs(signs @ x) >= observed - EPS)) + 1) / (self.n_resamples + 1)


def sign_flip_interval(deltas: list[float], p_zero: float | None, seed: int = 0, *,
                       rounded: bool = True) -> tuple[float | None, float | None]:
    """``(lo, hi)`` of one row (module docstring), ``None`` for an open end; rounded to 4 places unless not
    ``rounded``, even for an exact p: an end is only known within :data:`TOL`, so its further digits are noise.

    ``p_zero`` is the row's p, :func:`sign_flip_p` of ``deltas`` (``None``: every delta tied, no evidence
    against 0): it decides whether 0 is inside."""
    test, mean = ShiftTest(deltas, seed), math.fsum(deltas) / len(deltas)
    zero_inside, low, high = p_zero is None or p_zero >= ALPHA, min(*deltas, 0.0), max(*deltas, 0.0)
    # far is 1 past the extreme, or twice it from 1 on: 1e16 + 1.0 rounds back onto 1e16, all tied
    ends = (_end(test, mean, low - max(1.0, -low), zero_inside),
            _end(test, mean, high + max(1.0, high), zero_inside))
    return (_rounded(ends[0]), _rounded(ends[1])) if rounded else ends


def _end(test: ShiftTest, mean: float, far: float, zero_inside: bool) -> float | None:
    """The end between ``mean`` and ``far``: the last shift bisection finds not rejected; ``None`` if ``far`` is not."""
    inside, outside = mean, far
    if min(mean, far) < 0 < max(mean, far):  # 0 is on this side: the row's p places it
        inside, outside = (0.0, far) if zero_inside else (mean, 0.0)
    if outside == far and _accepts(test.p(far)):
        return None
    while abs(outside - inside) > TOL:
        mid = (inside + outside) / 2
        if mid in (inside, outside):  # no float left between them
            break
        inside, outside = (mid, outside) if _accepts(test.p(mid)) else (inside, mid)
    return inside


def _accepts(p: float | None) -> bool:
    return p is None or p >= ALPHA


def _rounded(end: float | None) -> float | None:
    """``end`` at 4 places, never rounded onto or across 0: the side of 0 an end is on is the row's verdict."""
    if end is None:
        return None
    out = round(end, 4)
    return math.copysign(1e-4, end) if out == 0 and end != 0 else out + 0.0  # + 0.0: no -0.0


def within(mean: float, lo: float | None, hi: float | None) -> float:
    """``mean`` held inside ``[lo, hi]``, an open end unbounded: :func:`_rounded` puts an end below 5e-5 at ±0.0001,
    past a mean 4 places put at 0, and a mean at fewer places can step out of a narrow interval."""
    return min(max(mean, -math.inf if lo is None else lo), math.inf if hi is None else hi)
