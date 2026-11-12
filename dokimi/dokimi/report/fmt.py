# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The number cells of the Markdown reports: one formatter per kind of number.

Each prints ``missing`` (an em dash unless the renderer passes its own mark) for a value that is not a
number, so a missing measurement never reads as a zero. A bool is not a number here (:func:`is_number`).
"""

from __future__ import annotations

from typing import Any

from dokimi.eval.sign_flip import fmt_delta, fmt_small
from dokimi.measure import is_number

DASH = "—"


def fixed(value: Any, digits: int = 1, *, missing: str = DASH) -> str:
    """``value`` at ``digits`` decimals."""
    return f"{value:.{digits}f}" if is_number(value) else missing


def signed(value: Any, digits: int = 1, *, missing: str = DASH) -> str:
    """``value`` at ``digits`` decimals with its sign, ``+`` included."""
    return f"{value:+.{digits}f}" if is_number(value) else missing


def percent(value: Any, digits: int = 0, *, missing: str = DASH) -> str:
    """A rate as a percentage: 0.125 is ``12%`` at 0 digits and ``12.5%`` at 1."""
    return f"{value * 100:.{digits}f}%" if is_number(value) else missing


def plain(value: Any, digits: int | None = None, *, missing: str = DASH) -> str:
    """``value`` as stored, a float at ``digits`` decimals when given; ``missing`` only for ``None``."""
    if value is None:
        return missing
    return f"{value:.{digits}f}" if digits is not None and isinstance(value, float) else str(value)


def usd(value: Any, *, missing: str = DASH) -> str:
    """A cost at 4 decimals; a real cost under $0.00005 is ``<$0.0001``, not ``$0.0000``."""
    if not is_number(value):
        return missing
    return "<$0.0001" if 0 < value < 0.00005 else f"${value:.4f}"


def usd_delta(value: Any, *, missing: str = DASH) -> str:
    """A cost difference at 4 decimals with its sign: ``$+0.0012``."""
    return f"${value:+.4f}" if is_number(value) else missing


def p_value(p: Any, *, missing: str = DASH) -> str:
    """A p-value at 4 decimals, ``<0.0001`` below that; ``missing`` means no test ran, which is not ``p = 1``."""
    if not is_number(p):
        return missing
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def verdict(significant: Any) -> str:
    """A significance verdict, ``—`` for ``None``: no p, or too few changed pairs for any p to reach 0.05.

    Printing ``no`` there would assert the absence of an effect on the strength of absent evidence.
    """
    if significant is None:
        return DASH
    return "yes" if significant else "no"


def delta(value: Any, *, missing: str = DASH) -> str:
    """A mean delta at one decimal, ``+`` when positive, by :func:`dokimi.eval.sign_flip.fmt_delta`."""
    return fmt_delta(value) if is_number(value) else missing


def ci_end(value: Any, *, missing: str = DASH) -> str:
    """A CI end, or the estimate beside the CI, at one decimal with its sign; a non-zero value that would print
    as 0.0 gets 2 significant digits, so it keeps its side of 0. The stored estimate lies inside its stored ends,
    so the printed one lies inside the printed interval (0.04 in [+0.04, +0.04])."""
    if not is_number(value):
        return missing
    text = f"{value:+.1f}"
    return fmt_small(value) if value and float(text) == 0 else text


def ci(lo: Any, hi: Any) -> str:
    """``[lo, hi]`` by :func:`ci_end`, an open end (``None``) as ``-inf`` / ``+inf``."""
    return f"[{'-inf' if lo is None else ci_end(lo)}, {'+inf' if hi is None else ci_end(hi)}]"
