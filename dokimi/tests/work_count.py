# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Work done from size n to 16n, counted instead of timed: the bound of the linear-scan tests that can count (#1061).

A test wraps a module-level function or compiled pattern that the code under test calls in a loop, with ``monkeypatch``,
and sums what each call is handed. The count has no noise: linear work gives 16, quadratic work 256.
A scan inside one regex or library call does not show in a count; those tests keep :func:`tests.growth_timing.growth`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.growth_timing import FACTOR

WORK_LIMIT = 24  # work at 16n over that at n: 16 for linear work, 256 for quadratic; 1.5x over linear leaves room for digits

_SCANS = ("search", "match", "fullmatch", "finditer", "findall")
_SUBS = ("sub", "subn")


class Tally:
    """What the wrapped callable was handed: ``size`` summed over its calls."""

    def __init__(self) -> None:
        self.size = 0
        self.calls = 0


class _CountedPattern:
    """A compiled pattern whose scans add the length of the text they read (from ``pos``) to the tally."""

    def __init__(self, pattern: Any, tally: Tally) -> None:
        self._pattern, self._tally = pattern, tally

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._pattern, name)
        if name not in _SCANS + _SUBS:
            return method

        def counted(*args: Any, **kwargs: Any) -> Any:
            if name in _SUBS:
                length = len(args[1] if len(args) > 1 else kwargs["string"])
            else:
                length = len(args[0] if args else kwargs["string"]) - (args[1] if len(args) > 1 else kwargs.get("pos", 0))
            self._tally.size += length
            self._tally.calls += 1
            return method(*args, **kwargs)
        return counted


def count_pattern(monkeypatch: Any, owner: Any, name: str) -> Tally:
    """Wrap the compiled pattern ``owner.name``: its scans add the text length they read."""
    tally = Tally()
    monkeypatch.setattr(owner, name, _CountedPattern(getattr(owner, name), tally))
    return tally


def count_calls(monkeypatch: Any, owner: Any, name: str,
                size: Callable[..., int] = lambda text, *rest, **kwargs: len(text)) -> Tally:
    """Wrap the function ``owner.name``: each call adds ``size(*args)``, the length of its first argument by default."""
    tally, real = Tally(), getattr(owner, name)

    def counted(*args: Any, **kwargs: Any) -> Any:
        tally.size += size(*args, **kwargs)
        tally.calls += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(owner, name, counted)
    return tally


def work_ratio(tally: Tally, run: Callable[[int], Any], n: int) -> float:
    """The tally's size after ``run(FACTOR * n)`` over that after ``run(n)``; the tally is reset before each."""
    sizes = []
    for size in (n, FACTOR * n):
        tally.size = tally.calls = 0
        run(size)
        sizes.append(tally.size)
    assert sizes[0] > 0, "the wrapped call was never reached"
    return sizes[1] / sizes[0]
