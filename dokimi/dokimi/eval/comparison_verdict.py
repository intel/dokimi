# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The verdict a comparison of two arms reads off: did the treatment move the measure, and which way.

A difference is read only when the caller's corrected test calls it significant. Its sign then gives the
direction. A tie, a missing delta and an uncorrected or non-significant result are all inconclusive.

This module decides; each caller keeps its own vocabulary (``improved`` in an evidence document, ``keep``
in a ledger) and its own reason text, and maps the verdict onto them.
"""

from __future__ import annotations

from enum import Enum


class ComparisonVerdict(str, Enum):
    """The direction of a significant difference, or ``INCONCLUSIVE``."""

    IMPROVED = "improved"
    REGRESSED = "regressed"
    INCONCLUSIVE = "inconclusive"


def decide(delta: float | None, significant: bool, *, eps: float = 0.0) -> ComparisonVerdict:
    """``IMPROVED`` or ``REGRESSED`` by the sign of ``delta``, when ``significant`` and ``abs(delta) > eps``.

    ``significant`` is the caller's already-decided test result: a corrected ``p < alpha``, or a bool the
    caller has made from its flag. It is read by truth value, so a NumPy bool counts. A ``None`` or NaN delta,
    and one at or below ``eps`` in magnitude, is inconclusive.
    """
    if delta is None or not significant or not abs(delta) > eps:
        return ComparisonVerdict.INCONCLUSIVE
    return ComparisonVerdict.IMPROVED if delta > 0 else ComparisonVerdict.REGRESSED
