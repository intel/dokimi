# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deltas and means of finite values that stay finite or are refused.

``treatment - baseline`` of two finite floats near the float range (``1.7e308 - -1.7e308``) is ``inf``, and a
sum of finite deltas can pass the range too. Carrying that ``inf`` into a sign-flip test or a row mean gives
a meaningless result, so :func:`checked_delta` and :func:`check_sum_range` raise ``error`` instead
(https://github.com/intel/dokimi/issues/639). A NaN or inf already in the input keeps its meaning in the delta.
Integers are exact: a difference or an integer beyond the float range is refused the same way.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _shown(value: float) -> str:
    """``value`` for a message; an integer of many digits is named by its length."""
    try:
        text = repr(value)
    except ValueError:  # an integer past Python's int-to-str digit limit
        return f"an integer of {int(math.log10(abs(value))) + 1} digits"
    return text if len(text) <= 24 else f"an integer of {len(text.lstrip('-'))} digits"


def _has_non_finite_float(*values: float) -> bool:
    return any(isinstance(v, float) and not math.isfinite(v) for v in values)


def checked_delta(treatment: float, baseline: float, error: type[Exception] = OverflowError, where: str = "") -> float:
    """``treatment - baseline``; raise ``error`` when both are finite and the difference is not.

    ``where`` names what the pair belongs to (a question) and leads the message.
    """
    try:
        delta = treatment - baseline
        refused = not math.isfinite(delta) and math.isfinite(treatment) and math.isfinite(baseline)
    except OverflowError:  # an integer too large for a float: the subtraction or ``isfinite`` cannot read it
        # an inf or NaN operand keeps its meaning; the integer only matters when both are finite
        refused = not _has_non_finite_float(treatment, baseline)
        delta = (treatment if isinstance(treatment, float) else 0.0) - (baseline if isinstance(baseline, float) else 0.0)
    if refused:
        raise error(f"{where}{': ' if where else ''}the difference of {_shown(treatment)} and {_shown(baseline)} leaves the float range")
    return delta


def check_sum_range(values: Sequence[float], error: type[Exception] = OverflowError, where: str = "") -> None:
    """Raise ``error`` when finite ``values`` have a sum of magnitudes beyond the float range; ``where`` leads the message.

    Such values can sum to ``inf`` at one point and back to a finite total at another, so no order of
    addition is safe for the sign patterns of a sign-flip test: they are refused.
    """
    try:
        refused = not math.isfinite(sum(abs(v) for v in values)) and all(math.isfinite(v) for v in values)
    except OverflowError:  # an integer sum too large for a float: ``isfinite`` cannot read it
        refused = not _has_non_finite_float(*values)
    if refused:
        raise error(f"{where}{': ' if where else ''}the sum of the magnitudes of the values leaves the float range")


def checked_mean(values: Sequence[float], error: type[Exception] = OverflowError, where: str = "") -> float:
    """``sum(values) / len(values)`` after :func:`check_sum_range`."""
    check_sum_range(values, error, where)
    if _has_non_finite_float(*values):  # they decide; finite values summed first could overflow and cancel a -inf
        return sum(v for v in values if isinstance(v, float) and not math.isfinite(v)) / len(values)
    return sum(values) / len(values)
