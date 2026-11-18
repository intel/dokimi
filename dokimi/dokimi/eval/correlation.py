# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Pearson correlation of two paired score vectors."""

from __future__ import annotations

from typing import Optional, Sequence


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson r of two equal-length vectors, rounded to 4 places.

    ``None`` when there are fewer than two pairs or one side is constant. Callers that
    need a larger minimum n (a correlation of two or three points says little) check it
    themselves.
    """
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    den = (sum(d * d for d in dx) ** 0.5) * (sum(d * d for d in dy) ** 0.5)
    if den == 0:
        return None
    return round(sum(a * b for a, b in zip(dx, dy, strict=True)) / den, 4)
