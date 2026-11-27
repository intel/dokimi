# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The text of a ``repo compare`` row's 95% interval (:mod:`.compare`).

The interval is every shift the row's sign-flip test does not reject: :func:`dokimi.eval.sign_flip.sign_flip_interval`
computes it, and its module docstring gives the method. This module holds what a row prints and checks of it.
"""

from __future__ import annotations


def includes_zero(lo: float | None, hi: float | None) -> bool:
    """Whether the interval holds 0, an open end unbounded."""
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def format_interval(lo: float | None, hi: float | None) -> str:
    """``[lo, hi]`` at 3 decimals, an open end as ``-inf`` or ``+inf``; a non-zero end 3 decimals would print as
    ``0.000`` gets 2 significant digits, so the text shows the side of 0 the row's verdict puts it on."""
    low = "-inf" if lo is None else format_value(lo, signed=True)
    high = "+inf" if hi is None else format_value(hi, signed=True)
    return f"[{low}, {high}]"


def format_value(value: float | None, signed: bool = False) -> str:
    """``value`` at 3 decimals, ``-`` for ``None``; a non-zero value 3 decimals would print as ``0.000`` gets 2
    significant digits (``1e-12``, ``-1e-12``) and keeps its sign. ``signed`` always writes the sign."""
    if value is None:
        return "-"
    text = f"{value:+.3f}" if signed else f"{value:.3f}"
    if value and float(text) == 0:
        return f"{value:+.2g}" if signed else f"{value:.2g}"
    return text
