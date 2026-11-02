# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The first useful result of a run, for ``result.json`` ``first_useful`` (contract §4.1).

The first event of the run that hits the task's definition (:mod:`.useful_spec`): a ``command`` event whose text
matches and whose ``rc`` is 0, or an ``edit`` event whose path matches a glob. A refused event (``denied``) never
hits, nor does an edit whose tool result is not a success (``rc`` 0 is the only success). ``steps`` counts every event
up to and including the hit, as ``process.tool_calls`` does. ``issued_s`` is the hit's ``t``: the time the call was
issued, since its result is not timed, so a test run's pass is not timed.
"""

from __future__ import annotations

import statistics
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .manifest import match_glob
from .process_counts import as_measure
from .process_metrics import _workspace_path
from .useful_spec import Useful

#: Seconds one ``command`` pattern may search one event's text. Past it the run gets reason ``timeout``: no verdict,
#: and the rest of its trace is not searched (a catastrophic pattern never stalls the host-side result).
USEFUL_REGEX_TIMEOUT = 2.0

#: The ``repo compare`` measures of the first useful result, one value per task (see :func:`useful_task_values`).
USEFUL_MEASURES = ("first_useful_s", "first_useful_steps", "first_useful_rate")

#: The reasons that give a cell no verdict on the first useful result (its trace was not read, or its pattern timed out).
_NO_VERDICT = ("unobserved", "timeout")

#: A drive-qualified (``C:/x``, ``c:x``) or backslash-rooted (``\\server\share``, ``\\x``) path: not a POSIX
#: path, so ``_workspace_path`` would take it for relative; it is outside the workspace.
_NON_POSIX_ROOT = re.compile(r"^(?:[A-Za-z]:|\\)")


def hits(useful: Useful, event: Mapping[str, Any]) -> bool:
    """Whether ``event`` satisfies ``useful``: a passing command matching the regex, or an edit matching a glob."""
    if event.get("denied"):  # a refused call never ran, or never changed the file
        return False
    # only a call whose result says it succeeded counts: a call with no result, or a failed one, changed nothing
    if event.get("rc") != 0:
        return False
    if useful.kind == "command":
        if event.get("kind") != "command" or useful.command is None:
            return False
        return useful.command.search(str(event.get("text") or ""), timeout=USEFUL_REGEX_TIMEOUT) is not None
    raw = str(event.get("path") or "")
    # no path, outside the workspace, or the workspace root itself: no target
    if event.get("kind") != "edit" or not raw or _NON_POSIX_ROOT.match(raw):
        return False
    path = _workspace_path(raw)
    if path is None or path == ".":
        return False
    return any(match_glob(g, path) for g in useful.globs)


def first_useful(
    useful: Useful | None, events: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """``result.json`` ``first_useful`` of one run; ``events`` is None when its trace could not be read."""
    if useful is None:
        return _record(None, "not_declared")
    if events is None:
        return _record(useful.kind, "unobserved")
    for steps, event in enumerate(events, 1):
        try:
            found = hits(useful, event)
        except TimeoutError:  # the pattern ran past its budget on this event: no verdict
            return _record(useful.kind, "timeout")
        if found:
            t = event.get("t")
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                return _record(useful.kind, None, issued_s=float(t), steps=steps)
            return _record(useful.kind, "untimed", steps=steps)  # a trace without timestamps
    return _record(useful.kind, "never")


def _record(
    kind: str | None, reason: str | None, issued_s: float | None = None, steps: int | None = None
) -> dict[str, Any]:
    """``reason`` says why ``issued_s`` or ``steps`` is null; it is null when the hit was found and timed."""
    return {"kind": kind, "reason": reason, "issued_s": issued_s, "steps": steps}


def _declared(recs: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The ``first_useful`` objects of the cells that record a definition: an older batch has none."""
    return [u for r in recs if isinstance(u := r.get("first_useful"), dict) and u.get("kind")]


def _observed(have: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The cells with a measurable ``steps``: the ones that observed a hit. Their ``issued_s`` is the only timing
    that goes with a hit; a cell that observed none has no seconds, even when a stored ``issued_s`` is a number."""
    return [u for u in have if as_measure(u.get("steps")) is not None]


def _measured(have: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The cells with a verdict: an ``unobserved`` or ``timeout`` cell has none, so it is no miss either."""
    return [u for u in have if u.get("reason") not in _NO_VERDICT]


def _nums(values: Iterable[Any]) -> list[float]:
    """The stored numbers that are measures: finite and within the report bound (a NaN or a 10**400 is no measure)."""
    return [num for v in values if (num := as_measure(v)) is not None]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def useful_summary(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """``repo report``'s ``first_useful`` for one row: the cells observed, their medians and why the rest are not.

    None when no cell records a definition."""
    have = _declared(recs)
    if not have:
        return None
    seconds = _nums(u.get("issued_s") for u in _observed(have))
    steps = _nums(u.get("steps") for u in have)
    kind = useful_kind(recs)  # "mixed" when the cells name both kinds: no median of those is one measure
    mixed = kind == "mixed"
    return {
        "kind": kind,
        "n": len(have),
        "observed": len(steps),
        "median_s": None if mixed else _median(seconds),
        "median_steps": None if mixed else _median(steps),
        "reasons": dict(Counter(u["reason"] for u in have if u.get("reason"))),
    }


def useful_kind(recs: Sequence[Mapping[str, Any]]) -> str | None:
    """The kind the declared cells of ``recs`` define: ``command`` or ``edit``; ``mixed`` when they name both; None
    when none declares one."""
    kinds = {u["kind"] for u in _declared(recs)}
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else "mixed"


def useful_task_values(recs: Sequence[Mapping[str, Any]]) -> dict[str, list[float | None]]:
    """``repo compare``'s per-task values: the medians over the cells that observed it, and the share of the cells
    with a verdict that observed it (None if none has one). A cell with no verdict (``unobserved``, ``timeout``) is
    neither a hit nor a miss. The medians alone see only the observed cells. Cells that name two kinds have no value:
    a command's timing and an edit's are not one measure."""
    have = _declared(recs)
    if useful_kind(recs) == "mixed":
        return {measure: [None] for measure in USEFUL_MEASURES}
    measured = _measured(have)
    observed = _nums(u.get("steps") for u in have)
    return {
        "first_useful_s": [_median(_nums(u.get("issued_s") for u in _observed(have)))],
        "first_useful_steps": [_median(observed)],
        "first_useful_rate": [
            len(_nums(u.get("steps") for u in measured)) / len(measured) if measured else None
        ],
    }


def useful_task_counts(recs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """The cells behind each :func:`useful_task_values` measure: the timed hits for seconds, the observed hits for
    steps, and the cells with a verdict for the rate (a median of one value is not one cell)."""
    have = _declared(recs)
    return {
        "first_useful_s": len(_nums(u.get("issued_s") for u in _observed(have))),
        "first_useful_steps": len(_nums(u.get("steps") for u in have)),
        "first_useful_rate": len(_measured(have)),
    }


def useful_counts(recs: Sequence[Mapping[str, Any]]) -> dict[str, int] | None:
    """One arm's cells that observed a first useful result, of those with a verdict, and the cells without one; None
    if none declares a definition."""
    have = _declared(recs)
    if not have:
        return None
    measured = _measured(have)
    return {
        "observed": len(_nums(u.get("steps") for u in measured)),
        "measured": len(measured),
        "unmeasured": len(have) - len(measured),
    }


def useful_arms_line(counts: Mapping[str, Mapping[str, int] | None]) -> str | None:
    """The compare header's line of observed-of-measured cells per arm, with the unmeasured ones; None when no arm
    declares one."""
    shown = [
        f"{arm} {c['observed']} of {c['measured']}"
        + (f" ({c['unmeasured']} unmeasured)" if c["unmeasured"] else "")
        for arm, c in counts.items() if c
    ]
    return f"first useful observed: {', '.join(shown)}" if shown else None


def useful_line(summary: Mapping[str, Any] | None, width: int) -> list[str]:
    """The row's first-useful summary on one indented line; none for a row without a definition."""
    if not summary:
        return []
    parts = [f"{summary['observed']} of {summary['n']} observed"]
    if summary["median_s"] is not None:
        parts.append(f"median {summary['median_s']:.1f} s")
    if summary["median_steps"] is not None:
        parts.append(f"{summary['median_steps']:g} steps")
    parts += [f"{reason} {count}" for reason, count in sorted(summary["reasons"].items())]
    return [" " * (width + 4) + f"first useful ({summary['kind']}): " + ", ".join(parts)]
