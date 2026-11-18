# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The paired sign-flip test and the 95% interval that inverts it, shared by ``repo compare`` and the arms track.

:func:`sign_flip_p` is the two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null,
over the deltas not within their tie width of 0 (:data:`EPS`, or :func:`tie_widths` of the pairs' values): exact up to :data:`EXACT_MAX` of them, Monte Carlo above.
:func:`min_sign_flip_p` is the floor it cannot go below over k changed deltas, ``2 / 2**k`` exact.

``p(theta)`` is the two-sided sign-flip p of the shifted deltas ``d - theta`` over every delta, as
:func:`sign_flip_p` computes it on them (that one adds the deltas as exact integers; this one, below, in floats, so its p can differ at the ends of a row whose deltas floats cannot resolve): a shifted delta within the tie width of its pair (:func:`tie_widths`, kept
from the original deltas) of 0 is a tie, so a delta tied at 0 counts again once ``theta`` moves off 0. Exact, a sign
pattern counts when its sum falls short of the observed one by at most the rounding error of adding the deltas summed (:func:`_sum_slack`; :data:`EPS` at most, and no more than the
largest tie width among them),
the observed sum taken as the least of its exactly rounded sum and the product's sums for
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
import math
import random
import statistics
import sys
from collections.abc import Sequence
from fractions import Fraction
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: The level p is read at (two-sided) and the interval is inverted at.
ALPHA = 0.05
#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties; for a pair whose values are below 1, ``EPS`` times the larger of them
#: (:func:`tie_widths`).
EPS = 1e-9
#: How close a bisected end comes to the step of ``p(theta)`` it stands for: far below the 3 printed decimals.
TOL = 1e-7
#: The interval's method name: ``delta.method`` of a ``repo compare`` row, ``ci_method`` of an arms result.
METHOD = "sign_flip_inversion"
#: A vector whose values are all below this is searched on a copy multiplied by a power of two (exact), so the
#: tie widths, the bisection step and the sums stay clear of the subnormal range.
_TINY = 2.0 ** -664


def pair_magnitudes(treatment: Sequence[float], baseline: Sequence[float]) -> list[float]:
    """The larger of each pair's two values in magnitude: the scale of the float noise in that pair's delta."""
    return [max(abs(t), abs(b)) for t, b in zip(treatment, baseline, strict=True)]


def tie_scale(magnitudes: Sequence[float] | None) -> float:
    """The scale the vector's values are read at: the largest finite magnitude, capped at 1. 1.0 where there is none
    to read (no magnitudes given, or all of them 0 or not finite)."""
    largest = max((m for m in magnitudes or () if math.isfinite(m)), default=0.0)
    return min(1.0, largest) if largest > 0.0 else 1.0


def tie_widths(magnitudes: Sequence[float] | None, n: int, k: int = 0) -> list[float]:
    """The width of a tie for each of ``n`` deltas: a delta is a tie when ``abs(delta) <= width``, then 0.

    Float noise in ``t - b`` scales with the values subtracted, not with the delta, so a pair with the larger value
    ``m`` (``magnitudes``) ties within ``EPS * min(1, m)``: ``EPS`` for values of 1 or more, as a score near 1 has it,
    and less below that. Each pair keeps its own width whatever the other deltas are. A pair whose values are 0 (or
    not finite) takes the width of :func:`tie_scale`. Without ``magnitudes`` every width is the absolute ``EPS``.
    ``k`` multiplies the widths by ``2**-k``, as the search on scaled deltas needs.
    """
    unit = tie_scale(magnitudes)
    if magnitudes is None:
        return [EPS * math.ldexp(1.0, -k)] * n
    return [EPS * math.ldexp(min(1.0, m) if math.isfinite(m) and m > 0.0 else unit, -k) for m in magnitudes]


def _sum_slack(summed: Sequence[float], cap: float) -> float:
    """How far two sign patterns' sums may differ and still be the same sum: the rounding error of adding the deltas
    in two orders (``n * 2**-52`` times their absolute sum), never more than ``cap``. A tie width, which is the noise
    of one pair's subtraction, is no slack on a sum: it would swallow a changed delta of the size of that width."""
    return min(cap, len(summed) * 2.0 ** -52 * math.fsum(abs(x) for x in summed))


def min_sign_flip_p(changed: int) -> float:
    """The smallest p :func:`sign_flip_p` can give over ``changed`` non-zero deltas, every sign the same: only that
    pattern and its mirror reach the observed ``|sum|``, ``2 / 2**changed`` exact, ``1 / (N + 1)`` Monte Carlo."""
    return min(1.0, 2 / 2 ** changed) if changed <= EXACT_MAX else 1 / (N_RESAMPLES + 1)


def sign_flip_p(deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES, *,
                exact: bool = False, magnitudes: Sequence[float] | None = None) -> float | None:
    """Two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null.

    ``None`` when every delta is a tie: there is no evidence either way, and 1.0 would read as a measured null.
    Rounded to 6 places as printed, unless ``exact``. The sums are exact: each delta is an integer multiple of one
    power of two, so a delta of 1e300 does not hide five of 1, and the p does not depend on the order or the Python
    that adds them. A pattern whose sum falls short of the observed one by at most the rounding slack of the deltas
    (:func:`_sum_slack`, never more than :data:`EPS`) still counts, as sums of decimals do (0.1 + 0.2 against 0.3). Deltas whose largest magnitude is above
    ``max float / (8 * n)`` (or whose values are all below 2**-664) are divided by a power of two (exact) first, as in
    :func:`sign_flip_interval`, so the tie cap and the interval's scale agree.

    ``magnitudes`` holds the larger value of each pair (:func:`pair_magnitudes`), which sets each tie width
    (:func:`tie_widths`); without it a tie is within :data:`EPS` of 0.
    """
    widths = tie_widths(magnitudes, len(deltas))
    active = [i for i, (x, w) in enumerate(zip(deltas, widths, strict=True)) if abs(x) > w]
    if not active:
        return None
    d = [deltas[i] for i in active]
    # the slack on a sum follows the pairs the sum is of, not a tied pair's larger values
    unit = tie_scale(None if magnitudes is None else [magnitudes[i] for i in active])
    k = _scale_exponent(d, unit)
    if k:  # the same p on the deltas divided by a power of two, as the interval searches them
        d = [math.ldexp(x, -k) for x in d]
    cap_eps = EPS * math.ldexp(unit, -k)
    if len(d) <= EXACT_MAX:
        p = _enumerated_p(d, cap_eps)
    else:
        ints, cap = _as_integers(d, cap_eps)
        observed = _observed(ints, cap)
        rng = random.Random(seed)
        hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in ints)) >= observed for _ in range(n_resamples))
        p = (hits + 1) / (n_resamples + 1)
    return p if exact else round(p, 6)


def _observed(ints: list[int], cap: int) -> int:
    """The observed ``|sum|`` less the rounding slack of the deltas (:func:`_sum_slack`, in the units of ``ints``)."""
    return abs(sum(ints)) - min(cap, (len(ints) * sum(map(abs, ints))) >> 52)


def _enumerated_p(d: list[float], cap_eps: float) -> float:
    """The exact p over every sign pattern of ``d``, whose sums are integers: a small delta is not lost against a large one."""
    ints, cap = _as_integers(d, cap_eps)
    observed = _observed(ints, cap)
    sums = [0]
    for x in ints:
        sums = [s + x for s in sums] + [s - x for s in sums]
    return sum(abs(s) >= observed for s in sums) / 2 ** len(d)


def _as_integers(values: Sequence[float], cap: float) -> tuple[list[int], int]:
    """``values`` and ``cap`` as integer multiples of one power of two, so that sums of them are exact; ``cap`` rounded down."""
    ratios = [v.as_integer_ratio() for v in values]
    denominator = max(den for _, den in ratios)
    return [num * (denominator // den) for num, den in ratios], math.floor(Fraction(cap) * denominator)


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
    can round to one side of it in the product and to the other in the exact sums of :func:`sign_flip_p`, so ``p(0)`` can differ there: the
    row's own p, not ``p(0)``, places 0 (:func:`sign_flip_interval`). Up to :data:`EXACT_MAX` shifted deltas that span more than
    40 binary orders of magnitude are summed exactly too, as floats lose the small ones there; more of them are not (#865)."""

    def __init__(self, deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES, *,
                 eps: float | Sequence[float] = EPS) -> None:
        import numpy as np

        widths = list(eps) if isinstance(eps, Sequence) else [eps] * len(deltas)  # a tie width per delta
        order = sorted(range(len(deltas)), key=lambda i: abs(deltas[i]) <= widths[i])  # changed first, stably
        self.changed = len([x for x, w in zip(deltas, widths, strict=True) if abs(x) > w])  # sign_flip_p's order first
        self.d = np.array([deltas[i] for i in order], dtype=float)
        self.widths = np.array([widths[i] for i in order], dtype=float)
        self.seed, self.n_resamples = seed, n_resamples

    def p(self, theta: float) -> float | None:
        """``None`` when every shifted delta ties, as :func:`sign_flip_p` has no p then."""
        import numpy as np

        x = self.d - theta
        tied = np.abs(x) <= self.widths
        x[tied] = 0.0
        moved = x[x != 0.0]
        if not moved.size:
            return None
        spread = math.frexp(float(np.abs(moved).max()))[1] - math.frexp(float(np.abs(moved).min()))[1]  # in binary orders
        if moved.size <= EXACT_MAX and spread > 40:
            return _enumerated_p(moved.tolist(), float(self.widths[~tied].max()))  # floats lose the small deltas
        eps = _sum_slack(moved.tolist(), float(self.widths[~tied].max()))
        observed = abs(math.fsum(moved.tolist()))  # exactly rounded: sum() rounds differently before 3.12
        if moved.size <= EXACT_MAX:
            sums = np.abs(_patterns(moved.size) @ moved)  # rows 0 and -1: the observed signs and their mirror
            least = min(observed, float(sums[0]), float(sums[-1]))  # the product rounds them otherwise from 1e6 on
            return int(np.count_nonzero(sums >= least - eps)) / 2 ** moved.size
        signs = _draws(self.seed, self.changed, self.d.size, self.n_resamples)
        return (int(np.count_nonzero(np.abs(signs @ x) >= observed - eps)) + 1) / (self.n_resamples + 1)


def sign_flip_interval(deltas: list[float], p_zero: float | None, seed: int = 0, *,
                       rounded: bool = True, magnitudes: Sequence[float] | None = None
                       ) -> tuple[float | None, float | None]:
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
    delta on one side, which the test rejects, so the search overshoots a true end at the maximum by a few ulps.

    ``magnitudes`` (:func:`pair_magnitudes`) set the tie widths (:func:`tie_widths`) and the bisection step, which
    scales with the largest of them below 1. Values all below 2**-664 are searched multiplied by a power of two, as
    huge ones are divided, so their widths and the step do not underflow; an end that comes back below the smallest
    float is that float, on its side of 0, never 0, since the end is off 0 whenever the row's p is below
    :data:`ALPHA`."""
    unit = tie_scale(magnitudes)
    k = _scale_exponent(deltas, unit)
    scaled_unit = math.ldexp(unit, -k)
    widths = tie_widths(magnitudes, len(deltas), k)
    far_unit = 1.0 if k > 0 else scaled_unit  # the far bound is 1 past the extreme, or the scale of tiny values
    if k:
        scaled = [math.ldexp(x, -k) for x in deltas]
        ends = _ends(scaled, p_zero, seed, widths, TOL * scaled_unit, far_unit)
        ends = (_scaled_back(ends[0], k), _scaled_back(ends[1], k))
    else:
        ends = _ends(deltas, p_zero, seed, widths, TOL * scaled_unit, far_unit)
    return (_rounded(ends[0]), _rounded(ends[1])) if rounded else ends


def _scale_exponent(deltas: list[float], unit: float = 1.0) -> int:
    """``k`` such that ``deltas / 2**k`` stays clear of the float limits in the search; 0 when they already do.

    A ``unit`` (the scale of the values, :func:`tie_scale`) below :data:`_TINY` makes ``k`` negative, bringing it
    into ``[0.5, 1)``."""
    if 0.0 < unit < _TINY:
        return math.frexp(unit)[1]
    largest, limit = max(map(abs, deltas), default=0.0), sys.float_info.max / (8 * len(deltas))
    if not math.isfinite(largest) or largest <= limit:
        return 0
    return math.frexp(largest)[1] - math.frexp(limit)[1] + 1


def _scaled_back(end: float | None, k: int) -> float | None:
    if end is None:
        return None
    try:
        out = math.ldexp(end, k)
        return math.copysign(5e-324, end) if out == 0.0 and end != 0.0 else out  # the smallest float, not 0
    except OverflowError:
        # a shift past the maximum puts every delta on one side and is rejected, so no true end is past it: the
        # bisection stops a few ulps beyond an end at the maximum, where the sums cannot resolve TOL
        return math.copysign(sys.float_info.max, end)


def _ends(deltas: list[float], p_zero: float | None, seed: int, eps: float | Sequence[float], tol: float,
          unit: float = 1.0) -> tuple[float | None, float | None]:
    test = ShiftTest(deltas, seed, eps=eps)
    mean = _start(test, math.fsum(deltas) / len(deltas))
    zero_inside, low, high = p_zero is None or p_zero >= ALPHA, min(*deltas, 0.0), max(*deltas, 0.0)
    # far is one unit (1, or the scale of tiny values) past the extreme, or twice it from one unit on:
    # 1e16 + 1.0 rounds back onto 1e16, all tied
    return (_end(test, mean, low - max(unit, -low), zero_inside, tol),
            _end(test, mean, high + max(unit, high), zero_inside, tol))


def _start(test: ShiftTest, mean: float) -> float:
    """The shift the bisection starts from: ``mean`` if the test accepts it, else the first accepted of the mean of the
    changed deltas and of up to :data:`EXACT_MAX` of the deltas, nearest the median first (shifted by a delta, that
    delta and the ones within their width of it tie). ``mean`` again if none is, as there is no accepted shift to find."""
    if not test.changed or _accepts(test.p(mean)):
        return mean
    changed = test.d[:test.changed].tolist()
    median = statistics.median(test.d.tolist())
    nearest = sorted(set(test.d.tolist()), key=lambda x: abs(x - median))[:EXACT_MAX]
    return next((c for c in [math.fsum(changed) / len(changed), *nearest] if _accepts(test.p(c))), mean)


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


def sign_flip_row(deltas: list[float], seed: int = 0, *, exact: bool,
                  magnitudes: Sequence[float] | None = None) -> dict:
    """The test of one row, as both row builders print it: :func:`eval.stats._describe_deltas` (arms) and
    :func:`repos.compare.sign_flip_summary` (repo compare). They shape their own keys around this.

    ``p`` is :func:`sign_flip_p` (rounded to 6 places unless ``exact``, as ``repo compare`` prints it). ``lo``/``hi``
    are the unrounded ends of the interval that inverts it, and ``ci_lo``/``ci_hi`` the same ends as printed
    (:func:`sign_flip_interval`, rounded). ``n_changed`` counts the deltas not within their :func:`tie_widths` of 0.
    ``magnitudes`` is :func:`pair_magnitudes` of the pairs the deltas come from; without it a tie is within
    :data:`EPS` of 0.
    """
    p = sign_flip_p(deltas, seed=seed, exact=exact, magnitudes=magnitudes)
    lo, hi = sign_flip_interval(deltas, p, seed=seed, rounded=False, magnitudes=magnitudes)
    return {"p": p, "lo": lo, "hi": hi, "ci_lo": _rounded(lo), "ci_hi": _rounded(hi),
            "n_changed": sum(abs(d) > w for d, w in zip(deltas, tie_widths(magnitudes, len(deltas)), strict=True))}


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
