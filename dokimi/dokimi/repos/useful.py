# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The first useful result of a run, for ``result.json`` ``useful`` (contract §4.1).

The first event of the run that hits the task's definition (:mod:`.useful_spec`): a ``command`` event whose text
matches and whose ``rc`` is 0, or an ``edit`` event whose path matches a glob. A refused event (``denied``) never
hits. ``steps`` counts every event up to and including the hit, as ``process.tool_calls`` does. ``seconds`` is the
hit's ``t``: the time the call was issued, since its result is not timed, so a test run's pass is not timed.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .manifest import match_glob
from .useful_spec import Useful

#: The ``repo compare`` measures of the first useful result, one value per task (see :func:`useful_task_values`).
USEFUL_MEASURES = ("first_useful_s", "first_useful_steps")


def hits(useful: Useful, event: Mapping[str, Any]) -> bool:
    """Whether ``event`` satisfies ``useful``: a passing command matching the regex, or an edit matching a glob."""
    if event.get("denied"):  # a refused call never ran, or never changed the file
        return False
    if useful.kind == "command":
        text = str(event.get("text") or "")
        matched = bool(useful.command and useful.command.search(text))
        return event.get("kind") == "command" and event.get("rc") == 0 and matched
    path = str(event.get("path") or "")
    return event.get("kind") == "edit" and any(match_glob(g, path) for g in useful.globs)


def first_useful(
    useful: Useful | None, events: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """``result.json`` ``useful`` of one run; ``events`` is None when its trace could not be read."""
    if useful is None:
        return _record(None, "not_declared")
    if events is None:
        return _record(useful.kind, "unobserved")
    for steps, event in enumerate(events, 1):
        if hits(useful, event):
            t = event.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                return _record(useful.kind, None, seconds=float(t), steps=steps)
            return _record(useful.kind, "untimed", steps=steps)  # a trace without timestamps
    return _record(useful.kind, "never")


def _record(
    kind: str | None, reason: str | None, seconds: float | None = None, steps: int | None = None
) -> dict[str, Any]:
    """``reason`` says why ``seconds`` or ``steps`` is null; it is null when the hit was found and timed."""
    return {"kind": kind, "reason": reason, "seconds": seconds, "steps": steps}


def _declared(recs: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The ``useful`` objects of the cells that record a definition: an older batch has none."""
    return [u for r in recs if isinstance(u := r.get("useful"), dict) and u.get("kind")]


def _nums(values: Iterable[Any]) -> list[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def useful_summary(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """``repo report``'s ``first_useful`` for one row: the cells observed, their medians and why the rest are not.

    None when no cell records a definition."""
    have = _declared(recs)
    if not have:
        return None
    seconds = _nums(u.get("seconds") for u in have)
    steps = _nums(u.get("steps") for u in have)
    return {
        "kind": have[0]["kind"],
        "n": len(have),
        "observed": len(steps),
        "median_seconds": _median(seconds),
        "median_steps": _median(steps),
        "reasons": dict(Counter(u["reason"] for u in have if u.get("reason"))),
    }


def useful_task_values(recs: Sequence[Mapping[str, Any]]) -> dict[str, list[float | None]]:
    """``repo compare``'s per-task values: the median over the cells that observed it, one value (None if none)."""
    have = _declared(recs)
    return {
        "first_useful_s": [_median(_nums(u.get("seconds") for u in have))],
        "first_useful_steps": [_median(_nums(u.get("steps") for u in have))],
    }


def useful_line(summary: Mapping[str, Any] | None, width: int) -> list[str]:
    """The row's first-useful summary on one indented line; none for a row without a definition."""
    if not summary:
        return []
    parts = [f"{summary['observed']} of {summary['n']} observed"]
    if summary["median_seconds"] is not None:
        parts.append(f"median {summary['median_seconds']:.1f} s")
    if summary["median_steps"] is not None:
        parts.append(f"{summary['median_steps']:g} steps")
    parts += [f"{reason} {count}" for reason, count in sorted(summary["reasons"].items())]
    return [" " * (width + 4) + f"first useful ({summary['kind']}): " + ", ".join(parts)]
