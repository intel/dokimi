# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``tests/growth_timing.py``, the bound of the linear-scan tests, on a coarse clock."""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from tests.growth_timing import growth


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
