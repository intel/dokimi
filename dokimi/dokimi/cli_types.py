# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The argparse ``type=`` validators commands share, one per kind of value (#229 P3-2).

A refusal raises ``argparse.ArgumentTypeError``: argparse prints ``argument --flag: <message>``
and exits 2. The flag vocabulary that names these kinds is ``cli_flags.py``.
"""

from __future__ import annotations

import argparse
import math

__all__ = ["non_negative_int", "positive_fraction", "positive_int", "positive_usd", "unit_interval"]


def _int_at_least(value: str, least: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if number < least:
        raise argparse.ArgumentTypeError(f"must be >= {least}, got {number}")
    return number


def positive_int(value: str) -> int:
    """An integer of at least 1; ``1.5`` and ``1e3`` are not integers."""
    return _int_at_least(value, 1)


def non_negative_int(value: str) -> int:
    """An integer of at least 0."""
    return _int_at_least(value, 0)


def positive_usd(value: str) -> float:
    """A positive, finite USD amount: ``0``, a negative, ``nan`` or ``inf`` would disable a spend cap."""
    refused = argparse.ArgumentTypeError(f"must be a positive number of USD, got {value!r}")
    try:
        usd = float(value)
    except ValueError:
        raise refused from None
    if not math.isfinite(usd) or usd <= 0:
        raise refused
    return usd


def positive_fraction(value: str) -> float:
    """A share above 0 and at most 1."""
    refused = argparse.ArgumentTypeError(f"expected a number in (0, 1], got {value!r}")
    try:
        number = float(value)
    except ValueError:
        raise refused from None
    if not 0 < number <= 1:  # nan fails both comparisons
        raise refused
    return number


def unit_interval(value: str) -> float:
    """A number from 0 to 1, both included."""
    refused = argparse.ArgumentTypeError(f"must be a number from 0 to 1, not {value}")
    try:
        number = float(value)
    except ValueError:
        raise refused from None
    if not 0 <= number <= 1:  # nan fails both comparisons
        raise refused
    return number
