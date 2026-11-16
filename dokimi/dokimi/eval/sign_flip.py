# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The paired sign-flip test and the 95% interval that inverts it, shared by ``repo compare`` and the arms track.

:func:`sign_flip_p` is the two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null,
over the deltas not within :func:`tie_tolerance` of 0 (:data:`EPS`, or less for a vector below it): exact up to :data:`EXACT_MAX` of them, Monte Carlo above.
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

This module imports nothing of :mod:`dokimi.eval.stats`, which imports it. It imports numpy where an interval is
computed, not at import: CI's static job imports the package (``config_check``, ``cli.py repo scan``) with its tools
only, no numpy, and :func:`sign_flip_p` needs none.
"""

from __future__ import annotations

import functools
import itertools
import math
import random
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: The level p is read at (two-sided) and the interval is inverted at.
ALPHA = 0.05
#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties: the cap of :func:`tie_tolerance`, which a vector whose largest
#: ``|delta|`` is below it scales down.
EPS = 1e-9
#: How close a bisected end comes to the step of ``p(theta)`` it stands for: far below the 3 printed decimals.
TOL = 1e-7
#: The interval's method name: ``delta.method`` of a ``repo compare`` row, ``ci_method`` of an arms result.
METHOD = "sign_flip_inversion"


def tie_scale(deltas: Sequence[float]) -> float:
    """The scale a vector of deltas is judged at: 1.0 unless its largest finite ``|delta|`` is below :data:`EPS`
    (and not 0), where it is that largest ``|delta|``. A vector of scores keeps the absolute :data:`EPS`."""
    largest = max((abs(x) for x in deltas if math.isfinite(x)), default=0.0)
    return largest if 0.0 < largest < EPS else 1.0


def tie_tolerance(deltas: Sequence[float]) -> float:
    """The width of a tie for this vector: a delta is a tie when ``abs(x) <= tie_tolerance(deltas)``.

    :data:`EPS` (1e-9, the float noise on a score near 1) for a vector whose largest finite ``|delta|`` is
    :data:`EPS` or more, so every result at score scale is the same as with the absolute rule; ``EPS`` times that
    largest ``|delta|`` below it, so a vector that varies at 1e-300 ties only its own float noise. The scale is the
    vector's, not a pair's. An all-zero vector (or none finite) keeps :data:`EPS`: every delta of it is a tie.
    The interval shifts the deltas by theta and takes the tolerance of the original vector, not of the shifted one.
    """
    return EPS * tie_scale(deltas)


def min_sign_flip_p(changed: int) -> float:
    """The smallest p :func:`sign_flip_p` can give over ``changed`` non-zero deltas, every sign the same: only that
    pattern and its mirror reach the observed ``|sum|``, ``2 / 2**changed`` exact, ``1 / (N + 1)`` Monte Carlo."""
    return min(1.0, 2 / 2 ** changed) if changed <= EXACT_MAX else 1 / (N_RESAMPLES + 1)


def sign_flip_p(deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES, *,
                exact: bool = False) -> float | None:
    """Two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null.

    ``None`` when every delta is a tie: there is no evidence either way, and 1.0 would read as a measured null.
    Rounded to 6 places as printed, unless ``exact``. Deltas whose largest magnitude is above ``max float / (8 * n)``
    are divided by a power of two (exact) first, as in :func:`sign_flip_interval`, so no sum leaves the float range.
    """
    eps = tie_tolerance(deltas)
    d = [x for x in deltas if abs(x) > eps]
    if not d:
        return None
    k = _scale_exponent(d)
    if k:  # near the float maximum the sums go to inf/nan: the same p on the deltas divided by a power of two
        d, eps = [math.ldexp(x, -k) for x in d], math.ldexp(eps, -k)
    observed = abs(sum(d)) - eps
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
    import numpy as np

    bits = (np.arange(2 ** k)[:, None] >> np.arange(k)) & 1
    out = 1.0 - 2.0 * bits
    out.flags.writeable = False
    return out


@functools.lru_cache(maxsize=2)
def _draws(seed: int, changed: int, n: int, n_resamples: int) -> np.ndarray:
    """``n_resamples x n`` random signs: the first ``changed`` columns are the draws of
    :func:`sign_flip_p`, resample by resample; the other columns continue the same stream."""
    import numpy as np

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

    def __init__(self, deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES, *,
                 eps: float | None = None) -> None:
        import numpy as np

        if eps is None:
            eps = tie_tolerance(deltas)
        changed = [x for x in deltas if abs(x) > eps]
        self.d = np.array(changed + [x for x in deltas if abs(x) <= eps], dtype=float)  # sign_flip_p's order first
        self.changed, self.seed, self.n_resamples, self.eps = len(changed), seed, n_resamples, eps

    def p(self, theta: float) -> float | None:
        """``None`` when every shifted delta ties, as :func:`sign_flip_p` has no p then."""
        import numpy as np

        x = self.d - theta
        eps = self.eps
        x[np.abs(x) <= eps] = 0.0
        moved = x[x != 0.0]
        if not moved.size:
            return None
        observed = abs(math.fsum(moved.tolist()))  # exactly rounded: sum() rounds differently before 3.12
        if moved.size <= EXACT_MAX:
            sums = np.abs(_patterns(moved.size) @ moved)  # rows 0 and -1: the observed signs and their mirror
            least = min(observed, float(sums[0]), float(sums[-1]))  # the product rounds them otherwise from 1e6 on
            return int(np.count_nonzero(sums >= least - eps)) / 2 ** moved.size
        signs = _draws(self.seed, self.changed, self.d.size, self.n_resamples)
        return (int(np.count_nonzero(np.abs(signs @ x) >= observed - eps)) + 1) / (self.n_resamples + 1)


def sign_flip_interval(deltas: list[float], p_zero: float | None, seed: int = 0, *,
                       rounded: bool = True) -> tuple[float | None, float | None]:
    """``(lo, hi)`` of one row (module docstring), ``None`` for an open end; rounded to 4 places unless not
    ``rounded``, even for an exact p: an end is only known within :data:`TOL`, so its further digits are noise.

    ``p_zero`` is the row's p, :func:`sign_flip_p` of ``deltas`` (``None``: every delta tied, no evidence
    against 0): it decides whether 0 is inside.

    Every delta tied gives ``(0.0, 0.0)`` from 6 deltas on, and ``p_zero`` is ``None``: no evidence against 0, but
    any shift off 0 moves all n deltas to one side, which the test rejects at ``2 / 2**n``, as eight +20 deltas give
    ``(20.0, 20.0)``. At 5 or fewer the ends are open, as at every row of so few.

    A largest ``|delta|`` above ``max float / (8 * n)`` would take the far bound, the shifted deltas or the pattern
    sums out of the float range: the search then runs on the deltas divided by a power of two (exact), with
    :data:`EPS` and :data:`TOL` divided the same, and each end is multiplied back. An end that comes
    back past the float maximum is the maximum (``±max``), never ``inf``: from 6 deltas on a shift past it puts every
    delta on one side, which the test rejects, so the search overshoots a true end at the maximum by a few ulps."""
    k, unit = _scale_exponent(deltas), tie_scale(deltas)  # the tie width and the search step are the vector's
    if k:
        scaled = [math.ldexp(x, -k) for x in deltas]
        ends = _ends(scaled, p_zero, seed, math.ldexp(EPS * unit, -k), math.ldexp(TOL * unit, -k), unit)
        ends = (_scaled_back(ends[0], k), _scaled_back(ends[1], k))
    else:
        ends = _ends(deltas, p_zero, seed, EPS * unit, TOL * unit, unit)
    return (_rounded(ends[0]), _rounded(ends[1])) if rounded else ends


def _scale_exponent(deltas: list[float]) -> int:
    """``k`` such that ``deltas / 2**k`` stays clear of the float maximum in the search; 0 when they already do."""
    largest, limit = max(map(abs, deltas), default=0.0),sys.float_info.max / (8 * len(deltas))
    if not math.isfinite(largest) or largest <= limit:
        return 0
    return math.frexp(largest)[1] - math.frexp(limit)[1] + 1


def _scaled_back(end: float | None, k: int) -> float | None:
    if end is None:
        return None
    try:
        return math.ldexp(end, k)
    except OverflowError:
        # a shift past the maximum puts every delta on one side and is rejected, so no true end is past it: the
        # bisection stops a few ulps beyond an end at the maximum, where the sums cannot resolve TOL
        return math.copysign(sys.float_info.max, end)


def _ends(deltas: list[float], p_zero: float | None, seed: int, eps: float, tol: float, unit: float = 1.0
          ) -> tuple[float | None, float | None]:
    test, mean = ShiftTest(deltas, seed, eps=eps), math.fsum(deltas) / len(deltas)
    zero_inside, low, high = p_zero is None or p_zero >= ALPHA, min(*deltas, 0.0), max(*deltas, 0.0)
    # far is one unit (1, or the scale of a vector below EPS) past the extreme, or twice it from one unit on:
    # 1e16 + 1.0 rounds back onto 1e16, all tied
    return (_end(test, mean, low - max(unit, -low), zero_inside, tol),
            _end(test, mean, high + max(unit, high), zero_inside, tol))


def _end(test: ShiftTest, mean: float, far: float, zero_inside: bool, tol: float = TOL) -> float | None:
    """The end between ``mean`` and ``far``: the last shift bisection finds not rejected; ``None`` if ``far`` is not."""
    inside, outside = mean, far
    if min(mean, far) < 0 < max(mean, far):  # 0 is on this side: the row's p places it
        inside, outside = (0.0, far) if zero_inside else (mean, 0.0)
    if outside == far and _accepts(test.p(far)):
        return None
    while abs(outside - inside) > tol:
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


def sign_flip_row(deltas: list[float], seed: int = 0, *, exact: bool) -> dict:
    """The test of one row, as both row builders print it: :func:`eval.stats._describe_deltas` (arms) and
    :func:`repos.compare.sign_flip_summary` (repo compare). They shape their own keys around this.

    ``p`` is :func:`sign_flip_p` (rounded to 6 places unless ``exact``, as ``repo compare`` prints it). ``lo``/``hi``
    are the unrounded ends of the interval that inverts it, and ``ci_lo``/``ci_hi`` the same ends as printed
    (:func:`sign_flip_interval`, rounded). ``n_changed`` counts the deltas not within :func:`tie_tolerance` of 0.
    """
    p = sign_flip_p(deltas, seed=seed, exact=exact)
    lo, hi = sign_flip_interval(deltas, p, seed=seed, rounded=False)
    tol = tie_tolerance(deltas)
    return {"p": p, "lo": lo, "hi": hi, "ci_lo": _rounded(lo), "ci_hi": _rounded(hi),
            "n_changed": sum(abs(d) > tol for d in deltas)}


def round_mean(mean: float) -> float:
    """``mean`` at 2 places, or at 2 significant digits where that would be 0.0: a mean of 0.0035 stays 0.0035."""
    return (round(mean, 2) or float(f"{mean:.2g}")) + 0.0  # + 0.0: no -0.0


def fmt_small(value: float) -> str:
    """A non-zero ``value`` under 0.05 in magnitude, signed, at 2 significant digits in fixed point: 1e-05 is +0.00001."""
    return f"{value:+.{1 - math.floor(math.log10(abs(value)))}f}".rstrip("0")


def fmt_delta(value: float) -> str:
    """``value`` at one decimal, ``+`` when positive; a non-zero one that would read 0.0 goes through :func:`fmt_small`."""
    rounded = round(value, 1)
    if value and not rounded:
        return fmt_small(value)
    return f"+{rounded}" if rounded > 0 else str(rounded)


def within(mean: float, lo: float | None, hi: float | None) -> float:
    """``mean`` held inside ``[lo, hi]``, an open end unbounded: :func:`_rounded` puts an end below 5e-5 at ±0.0001,
    past a mean 4 places put at 0, and a mean at fewer places can step out of a narrow interval."""
    return min(max(mean, -math.inf if lo is None else lo), math.inf if hi is None else hi)
