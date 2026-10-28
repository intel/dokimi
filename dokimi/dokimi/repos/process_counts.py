# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``result.json`` ``process`` counts (contract §4.1) as measures of ``repo report`` and ``repo compare``.

Each count is a number per cell: ``process:<key>`` for the framework's counts
(``guidance_read``, a list of paths, is counted) and ``counter:<name>`` for
each manifest ``process_metrics`` counter. A cell whose ``process`` is absent
(written before it existed) or ``null`` (its trace could not be read) has none
and is left out of a mean, not counted as zero; so is a value that is not a
finite number.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


#: ``result.json`` ``process`` keys that are counts (``guidance_read`` is a list of paths, counted).
PROCESS_KEYS = ("tool_calls", "commands", "failed_commands", "denied_commands", "guidance_read")


def process_values(rec: dict[str, Any]) -> dict[str, float]:
    """A cell's ``process`` counters as numbers: ``process:<key>`` and ``counter:<name>``; ``{}`` without one."""
    found = rec.get("process")
    if not isinstance(found, dict):
        return {}
    out = {}
    for key in PROCESS_KEYS:
        value = found.get(key)
        count = len(value) if key == "guidance_read" and isinstance(value, list) else value
        if (num := _count(count)) is not None:
            out[f"process:{key}"] = num
    counters = found.get("counters")
    for name, value in (counters.items() if isinstance(counters, dict) else ()):
        if (num := _count(value)) is not None:
            out[f"counter:{name}"] = num
    return out


def process_means(recs: list[dict[str, Any]]) -> dict[str, float]:
    """Per process measure, the mean over the cells that report it, in a stable order."""
    values: dict[str, list[float]] = defaultdict(list)
    for rec in recs:
        for key, value in process_values(rec).items():
            values[key].append(value)
    return {key: round(sum(v) / len(v), 4) for key, v in sorted(values.items(), key=lambda kv: process_order(kv[0]))}


def process_order(key: str) -> tuple[int, str]:
    """Sort key of process measures: the framework's counts in contract order, then the counters by name."""
    name = key.removeprefix("process:")
    return (PROCESS_KEYS.index(name), "") if name in PROCESS_KEYS else (len(PROCESS_KEYS), key)


def _count(value: Any) -> float | None:
    """A finite number as a float; ``None`` for anything else (a bool, a string, NaN)."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else None


def process_line(means: dict[str, float] | None, width: int) -> list[str]:
    """The row's process means on one indented line; none for a row without them."""
    if not means:
        return []
    return [" " * (width + 4) + "process " + ", ".join(f"{k.removeprefix('process:')} {v:g}" for k, v in means.items())]
