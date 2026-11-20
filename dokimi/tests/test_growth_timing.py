# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``tests/growth_timing.py``, the bound of the linear-scan tests, on a coarse clock."""

from __future__ import annotations

import gc
import math
from collections.abc import Callable

import pytest

from tests.growth_timing import STEPS, growth


class _SteppedClock:
    """A CPU clock that shows time in steps of 15.625 ms, as Windows' does; a read costs 1 µs."""

    STEP = 0.015625

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        self.seconds += 1e-6
        return math.floor(self.seconds / self.STEP) * self.STEP

    def work(self, seconds: float) -> Callable[[], float]:
        def call() -> float:
            self.seconds += seconds
            return seconds
        return call


@pytest.mark.parametrize("cost, low, high", [
    (lambda size: size * 1e-7, 3.6, 4.4),  # 0.1 ms at n, 0.4 ms at 4n: both under one step, under the limit
    (lambda size: size ** 2 * 1e-10, 14.4, 17.6),  # 0.1 ms at n, 1.6 ms at 4n: over the limit (8)
], ids=["linear", "quadratic"])
def test_growth_times_calls_shorter_than_a_clock_step(cost, low, high):
    clock = _SteppedClock()
    ratio, result = growth(lambda size: clock.work(cost(size)), 1_000, clock=clock)
    assert low < ratio < high and result == cost(4_000)


def _run(cost, slowed=(), slowed_n=()):
    """``growth`` over ``cost``, the 4n samples of attempt ``i`` ``slowed[i]`` times dearer, the n samples ``slowed_n[i]`` (1 past a tuple).

    Returns the ratio, the result and the attempts made: the 4n calls over those of one attempt, ``reps``
    samples of ``loops`` calls, ``loops`` the power of two that makes the n call span :data:`STEPS` steps.
    """
    clock, calls, reps, loops = _SteppedClock(), [0], 5, 1
    while loops * cost(1_000) < STEPS * clock.STEP:
        loops *= 2
    per_attempt = reps * loops

    def timed(size):
        factors = slowed_n if size == 1_000 else slowed
        def call():
            attempt = calls[0] // per_attempt
            calls[0] += size != 1_000
            return clock.work(cost(size) * (factors[attempt] if attempt < len(factors) else 1.0))()
        return call

    ratio, result = growth(timed, 1_000, reps=reps, clock=clock)
    return ratio, result, calls[0] / per_attempt


def test_growth_measures_again_when_the_first_attempt_was_slowed():
    """#1007: a burst of load on the 4n samples lifts the first ratio to 9; the next attempt's 4 is returned."""
    ratio, result, attempts = _run(lambda size: size * 1e-7, slowed=(2.25,))
    assert 3.6 < ratio < 4.4 and result == 4_000 * 1e-7 and attempts == 2


def test_growth_keeps_the_best_sample_of_each_size_over_the_attempts():
    """#1007: when every attempt is slowed (9, 12, 10), the best 4n sample, the first attempt's, gives 9, not the last ratio."""
    ratio, result, attempts = _run(lambda size: size * 1e-7, slowed=(2.25, 3.0, 2.5))
    assert 8.6 < ratio < 9.4 and result == 4_000 * 1e-7 * 2.5 and attempts == 3


def test_growth_of_quadratic_work_is_not_hidden_by_a_burst_on_the_n_samples():
    """#1007: quadratic work with the 4n samples 1.5x slow (ratio 24), then the n samples 2.25x slow (7.11), still shows 16."""
    ratio, _, attempts = _run(lambda size: size ** 2 * 1e-10, slowed=(1.5,), slowed_n=(1.0, 2.25))
    assert 14.4 < ratio < 17.6 and attempts == 3


def test_growth_of_quadratic_work_stays_near_16_after_all_attempts():
    """#1007: the retry does not hide a real regression: every attempt shows about 16, and three are made."""
    ratio, result, attempts = _run(lambda size: size ** 2 * 1e-10)
    assert 14.4 < ratio < 17.6 and result == 4_000 ** 2 * 1e-10 and attempts == 3


def test_growth_of_linear_work_is_measured_once():
    """#1007: a first attempt under the limit is not repeated, so the retry adds no time on a quiet runner."""
    ratio, _, attempts = _run(lambda size: size * 1e-7)
    assert 3.6 < ratio < 4.4 and attempts == 1


@pytest.mark.parametrize("enabled", [True, False], ids=["on", "off"])
def test_growth_times_with_gc_off_and_leaves_it_as_the_caller_had_it(enabled):
    clock, seen = _SteppedClock(), set()
    def timed(size):
        work = clock.work(size * 1e-7)
        return lambda: seen.add(gc.isenabled()) or work()
    (gc.enable if enabled else gc.disable)()
    try:
        growth(timed, 1_000, clock=clock)
        after = gc.isenabled()
    finally:
        gc.enable()
    assert seen == {False} and after is enabled
