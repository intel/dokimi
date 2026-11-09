# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How much a call's CPU time grows from size n to 4n: the bound of the linear-scan tests (#505)."""

from __future__ import annotations

import gc
import time
from collections.abc import Callable
from typing import Any

GROWTH_LIMIT = 8  # CPU time at 4n over that at n: about 4 for linear work, 16 for quadratic
STEPS = 20  # a sample spans at least this many clock steps, so one step is at most 5% of it


def clock_step(clock: Callable[[], float] = time.process_time) -> float:
    """The smallest advance ``clock`` shows: about a microsecond on Linux, about 16 ms on Windows."""
    start = clock()
    while (now := clock()) == start:
        pass
    return now - start


def _sample(clock: Callable[[], float], call: Callable[[], Any], loops: int) -> tuple[float, Any]:
    """CPU time of ``loops`` runs of ``call``, GC off, and the last run's result; GC is left as it was."""
    enabled = gc.isenabled()
    gc.disable()
    try:
        start, result = clock(), None
        for _ in range(loops):
            result = call()
        return clock() - start, result
    finally:
        if enabled:
            gc.enable()


def growth(timed: Callable[[int], Callable[[], Any]], n: int, reps: int = 5,
           clock: Callable[[], float] = time.process_time) -> tuple[float, Any]:
    """CPU time of the call ``timed(4 * n)`` returns over that of ``timed(n)``'s, and the call's result.

    A sample runs a call ``loops`` times, ``loops`` doubled until the call at n spans :data:`STEPS`
    steps of ``clock``: a coarse clock (Windows) times a short call as 0. Both sizes run as many
    loops; the best of ``reps`` interleaved samples each: a slow or busy runner slows both sizes,
    not the ratio (#505).
    """
    calls = [timed(n), timed(4 * n)]
    floor, loops = STEPS * clock_step(clock), 1
    while _sample(clock, calls[0], loops)[0] < floor:
        loops *= 2
    best, result = [float("inf")] * 2, None
    for _ in range(reps):
        for k, call in enumerate(calls):
            elapsed, result = _sample(clock, call, loops)
            best[k] = min(best[k], elapsed)
    return best[1] / best[0], result
