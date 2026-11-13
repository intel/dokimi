# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deltas and means of finite values that stay finite or are refused.

``treatment - baseline`` of two finite floats near the float range (``1.7e308 - -1.7e308``) is ``inf``, and a
sum of finite deltas can pass the range too. Carrying that ``inf`` into a sign-flip test or a row mean gives
a meaningless result, so :func:`checked_delta` and :func:`checked_mean` raise ``error`` instead
(https://github.com/intel/dokimi/issues/639). A NaN or inf already in the input keeps its meaning in the delta.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def checked_delta(treatment: float, baseline: float, error: type[Exception] = OverflowError) -> float:
    """``treatment - baseline``; raise ``error`` when both are finite and the difference is not."""
    delta = treatment - baseline
    if not math.isfinite(delta) and math.isfinite(treatment) and math.isfinite(baseline):
        raise error(f"the difference of {treatment!r} and {baseline!r} leaves the float range")
    return delta


def checked_mean(values: Sequence[float], error: type[Exception] = OverflowError) -> float:
    """``sum(values) / len(values)``; raise ``error`` when finite values have a sum of magnitudes beyond the range.

    Such values can sum to ``inf`` at one point and back to a finite total at another, so no order of
    addition is safe for the sign patterns of a sign-flip test: the mean is refused with them.
    """
    if not math.isfinite(sum(abs(v) for v in values)) and all(math.isfinite(v) for v in values):
        raise error("the sum of the magnitudes of the values leaves the float range")
    return sum(values) / len(values)
