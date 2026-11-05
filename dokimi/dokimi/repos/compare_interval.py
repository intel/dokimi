# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The 95% interval of a ``repo compare`` row: every shift the row's sign-flip test does not reject (:mod:`.compare`).

``p(theta)`` is the two-sided sign-flip p of the shifted deltas ``d - theta`` over every task, as
:func:`~.compare.sign_flip_p` computes it on them: a shifted delta within :data:`EPS` of 0 is a tie, so a task tied
at 0 counts again once ``theta`` moves off 0. The interval is every ``theta`` with ``p(theta)`` at least
:data:`~.compare_reach.ALPHA`, uncorrected; each end is bisected between the mean (where p is 1) and a far bound past
every delta and 0 (1 past the extreme, twice it from 1 on), where the shifted deltas share one sign and p is at its floor, ``2 / 2**n`` over n tasks exact.
So an end is open (``None``) at 5 or fewer tasks, and both are bounded from 6 tasks on, whatever the ties.

Whether 0 is inside is the row's own p's call (the caller passes it): 0 is inside exactly when that p is at least
ALPHA, so the interval excludes 0 exactly when p < ALPHA. Exact up to :data:`EXACT_MAX` non-zero shifted deltas,
with one cached ``2**k x k`` sign matrix per k and one matrix-vector product per theta; Monte Carlo above, with one
:data:`N_RESAMPLES` x n sign matrix drawn once from the seed and reused at every theta (common random numbers), so the
ends are the same from run to run. Its columns of the changed tasks are the draws :func:`~.compare.sign_flip_p`
makes for that seed, so the Monte Carlo ``p(0)`` is the printed p. ``p(theta)`` is a step function and the ends are
its step points, within :data:`TOL`: there is no mid-p, which would no longer agree with the exact p a row prints.
"""

from __future__ import annotations

import functools
import math
import random

import numpy as np

from .compare_reach import ALPHA

#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties.
EPS = 1e-9
#: How close a bisected end comes to the step of ``p(theta)`` it stands for: far below the 3 printed decimals.
TOL = 1e-7
#: The ``delta.method`` of every row.
METHOD = "sign_flip_inversion"


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
    :func:`~.compare.sign_flip_p`, resample by resample; the other columns continue the same stream."""
    key = random.Random(seed).getstate()[1]
    stream = np.random.RandomState()  # random.Random's Mersenne Twister, its state copied: the same doubles
    stream.set_state(("MT19937", np.array(key[:624], dtype=np.uint32), int(key[624]), 0, 0.0))
    first = stream.random_sample((n_resamples, changed))
    out = np.where(np.hstack([first, stream.random_sample((n_resamples, n - changed))]) < 0.5, 1.0, -1.0)
    out.flags.writeable = False
    return out


class ShiftTest:
    """The sign-flip p of ``deltas - theta`` over every task at any ``theta``, unrounded; at 0 it is
    :func:`~.compare.sign_flip_p` of ``deltas``, Monte Carlo draws included."""

    def __init__(self, deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES) -> None:
        changed = [x for x in deltas if abs(x) > EPS]
        self.d = np.array(changed + [x for x in deltas if abs(x) <= EPS], dtype=float)  # sign_flip_p's order first
        self.changed, self.seed, self.n_resamples = len(changed), seed, n_resamples

    def p(self, theta: float) -> float | None:
        """``None`` when every shifted delta ties, as :func:`~.compare.sign_flip_p` has no p then."""
        x = self.d - theta
        x[np.abs(x) <= EPS] = 0.0
        moved = x[x != 0.0]
        if not moved.size:
            return None
        observed = abs(sum(moved.tolist())) - EPS  # summed as sign_flip_p sums, in its order
        if moved.size <= EXACT_MAX:
            return int(np.count_nonzero(np.abs(_patterns(moved.size) @ moved) >= observed)) / 2 ** moved.size
        signs = _draws(self.seed, self.changed, self.d.size, self.n_resamples)
        return (int(np.count_nonzero(np.abs(signs @ x) >= observed)) + 1) / (self.n_resamples + 1)


def sign_flip_interval(deltas: list[float], p_zero: float | None, seed: int = 0, *,
                       exact: bool = False) -> tuple[float | None, float | None]:
    """``(lo, hi)`` of one row (module docstring), ``None`` for an open end; rounded to 4 places unless ``exact``.

    ``p_zero`` is the row's p, :func:`~.compare.sign_flip_p` of ``deltas`` (``None``: every delta tied, no evidence
    against 0): it decides whether 0 is inside."""
    test, mean = ShiftTest(deltas, seed), sum(deltas) / len(deltas)
    zero_inside, low, high = p_zero is None or p_zero >= ALPHA, min(*deltas, 0.0), max(*deltas, 0.0)
    # far is 1 past the extreme, or twice it from 1 on: 1e16 + 1.0 rounds back onto 1e16, all tied
    ends = (_end(test, mean, low - max(1.0, -low), zero_inside),
            _end(test, mean, high + max(1.0, high), zero_inside))
    return ends if exact else (_rounded(ends[0]), _rounded(ends[1]))


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


def includes_zero(lo: float | None, hi: float | None) -> bool:
    """Whether the interval holds 0, an open end unbounded."""
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def format_interval(lo: float | None, hi: float | None) -> str:
    """``[lo, hi]`` at 3 decimals, an open end as ``-inf`` or ``+inf``."""
    return f"[{'-inf' if lo is None else f'{lo:+.3f}'}, {'+inf' if hi is None else f'{hi:+.3f}'}]"
