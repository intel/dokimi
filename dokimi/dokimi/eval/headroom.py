# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Headroom: the items of an arms comparison that cannot tell a skill apart.

An item is a **ceiling** when its no-skill arm already scores at least ``ceiling`` (0-1: the
judge's or the grader's 0-100 aggregate divided by 100). A skill cannot raise it, so the item
measures nothing about the skill. An item is a **floor** when every arm scores at most
``floor`` (default 0): no arm answers it, which points at the question, its checks or the
grader rather than at any skill.

An item with fewer than ``min_reps`` runs behind it is ``provisional``: one run's flag may be
noise. Its reps are the smallest number of runs in which any of its arms was scored, so a single
artifact gives every item one rep. Each arm's score is the mean over the runs that scored it.

A run is one ``arms.v1`` file (or any artifact :mod:`dokimi.records` reads as an arms run), one
per repetition of the same question set. Each ``Run`` passed here counts as one run, so callers
drop repeated inputs first (the CLI does, by content digest). A question id repeated within one
run is refused: it would count as two runs. Nothing here draws a confidence interval; the paired
statistics of a comparison stay in :mod:`dokimi.eval.stats`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from dokimi.records import Run, arm_names

DEFAULT_CEILING = 0.9
DEFAULT_FLOOR = 0.0
DEFAULT_MIN_REPS = 3

_EPS = 1e-9  # float error bound for a threshold comparison on a mean of 0-1 scores

CEILING = "ceiling"
FLOOR = "floor"


@dataclass(frozen=True)
class ItemHeadroom:
    question_id: str
    reps: int
    provisional: bool
    no_skill: Optional[float]
    arms: Dict[str, Optional[float]]
    flag: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "reps": self.reps,
            "provisional": self.provisional,
            "no_skill": self.no_skill,
            "arms": dict(self.arms),
            "flag": self.flag,
        }


def check_thresholds(ceiling: float, floor: float, min_reps: int) -> None:
    """Refuse thresholds that cannot flag anything sensibly (ValueError)."""
    if not 0.0 < ceiling <= 1.0:
        raise ValueError(f"ceiling must be in (0, 1], not {ceiling!r}")
    if not 0.0 <= floor < ceiling:
        raise ValueError(f"floor must be in [0, ceiling), not {floor!r} (ceiling {ceiling!r})")
    if min_reps < 1:
        raise ValueError(f"min_reps must be at least 1, not {min_reps!r}")


def _mean(values: List[float]) -> Optional[float]:
    # Unrounded: the thresholds compare the exact mean (a 89.996 item is not a ceiling).
    return sum(values) / len(values) if values else None


def _flag(no_skill: Optional[float], arms: Dict[str, Optional[float]], ceiling: float, floor: float) -> Optional[str]:
    # The mean of 0-1 scores is float division: a mean that is exactly the threshold can land a
    # few ulps off it. The tolerance is far below one score step (0.01 at the finest), so it only
    # absorbs that error.
    if no_skill is not None and no_skill >= ceiling - _EPS:
        return CEILING
    if arms and all(v is not None and v <= floor + _EPS for v in arms.values()):
        return FLOOR
    return None


def headroom(
    runs: Sequence[Run],
    *,
    no_skill_arm: Optional[str] = None,
    ceiling: float = DEFAULT_CEILING,
    floor: float = DEFAULT_FLOOR,
    min_reps: int = DEFAULT_MIN_REPS,
) -> Dict[str, Any]:
    """Flag every item of ``runs`` (arms runs of one question set) by its headroom.

    ``no_skill_arm`` defaults to the baseline the runs declare, and must otherwise be an arm
    the runs list. Raises ValueError for thresholds that cannot be used, a run that is not an
    arms run, a question id repeated within a run, an unknown no-skill arm, or runs that
    disagree on the baseline when none is given.
    """
    check_thresholds(ceiling, floor, min_reps)
    control = _no_skill_arm(runs, no_skill_arm)
    scores, arms_of = _collect(runs)

    items: List[ItemHeadroom] = []
    for qid in sorted(scores, key=_sort_key):
        per_arm = scores[qid]
        arms = {arm: _mean(per_arm.get(arm, [])) for arm in sorted(arms_of[qid])}
        reps = min((len(per_arm.get(arm, [])) for arm in arms_of[qid]), default=0)
        no_skill = arms.get(control)
        items.append(ItemHeadroom(
            question_id=qid,
            reps=reps,
            provisional=reps < min_reps,
            no_skill=no_skill,
            arms=arms,
            flag=_flag(no_skill, arms, ceiling, floor),
        ))

    summary = {
        "items": len(items),
        "ceiling": sum(1 for i in items if i.flag == CEILING),
        "floor": sum(1 for i in items if i.flag == FLOOR),
        "provisional": sum(1 for i in items if i.provisional),
        "flagged_provisional": sum(1 for i in items if i.flag and i.provisional),
        "no_flag": sum(1 for i in items if i.flag is None),
    }
    return {
        "no_skill_arm": control,
        "thresholds": {"ceiling": ceiling, "floor": floor, "min_reps": min_reps},
        "runs": len(runs),
        "items": [i.as_dict() for i in items],
        "summary": summary,
    }


def _no_skill_arm(runs: Sequence[Run], given: Optional[str]) -> str:
    """The control arm: ``given``, or the one baseline all runs declare. Refuses anything else."""
    if not runs:
        raise ValueError("no runs to read")
    for run in runs:
        if run.shape != "arms":
            raise ValueError("headroom reads arms runs only; a binary answers or eval file was given")
    if given is not None:
        known = _known_arms(runs)
        if given not in known:
            listed = ", ".join(sorted(known)) or "none"
            raise ValueError(f"no-skill arm {given!r} is not an arm of these runs; the arms are: {listed}")
        return given
    declared = {run.baseline for run in runs}
    only = next(iter(declared)) if len(declared) == 1 else None
    if only is None:
        raise ValueError("the runs do not declare one baseline arm; pass --no-skill-arm")
    return only


def _known_arms(runs: Sequence[Run]) -> Set[str]:
    """Every arm any run declares or any record lists, ``null`` ones included."""
    known: Set[str] = set()
    for run in runs:
        known.update(run.conditions)
        for rec in run:
            known.update(arm_names(rec))
    return known


def repeated_ids(run: Run) -> List[str]:
    """The question ids that more than one record of ``run`` carries, in first-seen order."""
    seen: Set[str] = set()
    repeated: List[str] = []
    for rec in run:
        if rec.key in seen and rec.key not in repeated:
            repeated.append(rec.key)
        seen.add(rec.key)
    return repeated


def _collect(runs: Sequence[Run]) -> Tuple[Dict[str, Dict[str, List[float]]], Dict[str, Set[str]]]:
    """Question id -> arm -> the per-run scores (0-1), and question id -> the arms it listed."""
    scores: Dict[str, Dict[str, List[float]]] = {}
    arms_of: Dict[str, Set[str]] = {}
    for index, run in enumerate(runs, start=1):
        dupes = repeated_ids(run)
        if dupes:
            raise ValueError(
                f"run {index} lists question_id {dupes[0]!r} more than once; "
                "a run holds each question once (a repeat would count as a second run)")
        for rec in run:
            names = set(arm_names(rec)) | set(rec.scores)
            arms_of.setdefault(rec.key, set()).update(names)
            per_arm = scores.setdefault(rec.key, {})
            for arm in names:
                value = rec.score(arm)
                if value is not None:
                    per_arm.setdefault(arm, []).append(float(value) / 100.0)
    return scores, arms_of


def _sort_key(qid: str):
    if qid.isdigit() and not qid.isascii():
        # isdigit() accepts superscripts and other digits int() refuses: name the id, do not crash.
        raise ValueError(f"question id {qid!r} uses non-ASCII digits; use ASCII digits or a non-numeric id")
    return (0, int(qid), qid) if qid.isdigit() else (1, 0, qid)


def format_table(result: Dict[str, Any]) -> str:
    """The human-readable table: one row per flagged or provisional item, then the counts."""
    control = result["no_skill_arm"]
    arm_names_ = sorted({a for item in result["items"] for a in item["arms"]} - {control})
    head = ["question", "reps", "no-skill", *arm_names_, "flag", "provisional"]
    rows = []
    for item in result["items"]:
        if item["flag"] is None and not item["provisional"]:
            continue
        cells = [
            str(item["question_id"]), str(item["reps"]), _fmt(item["no_skill"]),
            *[_fmt(item["arms"].get(a)) for a in arm_names_],
            item["flag"] or "-", "yes" if item["provisional"] else "no",
        ]
        rows.append(cells)
    widths = [max(len(r[c]) for r in [head, *rows]) for c in range(len(head))] if rows else [len(h) for h in head]
    lines = [_line(head, widths)] + [_line(r, widths) for r in rows]
    if not rows:
        lines.append("(no item is flagged or provisional)")
    s = result["summary"]
    th = result["thresholds"]
    lines.append("")
    lines.append(
        f"{s['items']} items; no-skill arm {result['no_skill_arm']!r}; ceiling >= {th['ceiling']:g}, "
        f"floor <= {th['floor']:g}, provisional below {th['min_reps']} reps")
    lines.append(
        f"ceiling {s['ceiling']}, floor {s['floor']}, provisional {s['provisional']} "
        f"({s['flagged_provisional']} of them flagged), unflagged {s['no_flag']}")
    return "\n".join(lines)


def _fmt(value: Optional[float]) -> str:
    # Rounded down, with the flag's tolerance: 0.89996 shows 0.89, an exact 0.9 shows 0.90.
    return "-" if value is None else f"{math.floor((value + _EPS) * 100) / 100:.2f}"


def _line(cells: List[str], widths: List[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()
