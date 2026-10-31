# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a JSON value is as a measurement: the one test the gate, the score, the report, the manifest,
the process counts and the reference checks share.

``json.loads`` reads ``NaN``, ``Infinity`` and integers of any size, and a bool is an int in Python.
A NaN compares false both ways and an infinity passes any bound, so neither may stand for a result;
an int past the float range makes ``float()`` raise ``OverflowError``, so ``is_finite`` never converts,
and ``as_float`` converts with that error caught and returned as ``None``.
"""

from __future__ import annotations

import math
from typing import Any

#: The largest |score| `repo report` reads: squared deltas and sums stay finite. A score past it is no measurement.
SCORE_BOUND = 1e150


def is_number(value: Any) -> bool:
    """An int or a float, and not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite(value: Any) -> bool:
    """:func:`is_number` and not NaN or infinite. An int is always finite, however large, so it is not converted."""
    return is_number(value) and (isinstance(value, int) or math.isfinite(value))


def as_float(value: Any, bound: float = math.inf) -> float | None:
    """``value`` as a float when it is a finite number with ``abs(value) <= bound``; ``None`` otherwise.

    ``None`` covers a bool, a string, NaN, an infinity, an int past the float range and a number past ``bound``.
    """
    if not is_number(value):
        return None
    try:
        num = float(value)
    except OverflowError:  # an int too large for a float
        return None
    return num if math.isfinite(num) and abs(num) <= bound else None
