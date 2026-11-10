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
    return f"[{'-inf' if lo is None else _end_text(lo)}, {'+inf' if hi is None else _end_text(hi)}]"


def _end_text(end: float) -> str:
    text = f"{end:+.3f}"
    return f"{end:+.2g}" if end and float(text) == 0 else text
