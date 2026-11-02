# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Headroom: the items of an arms comparison that cannot tell a skill apart.

An item is a **ceiling** when its no-skill arm already scores at least ``ceiling`` (0-1: the
judge's or the grader's 0-100 aggregate divided by 100). A skill cannot raise it, so the item
measures nothing about the skill. An item is a **floor** when every arm scores at most
``floor`` (default 0): no arm answers it, which points at the question, its checks or the
grader rather than at any skill.

An item's floor test leaves out an arm no run scored (listed in ``unscored_arms``). It needs the
no-skill arm scored on the item and at least one arm other than the no-skill arm that did score:
the no-skill arm alone at zero shows nothing about a skill, and an unscored no-skill arm shows
nothing either.

An item with fewer than ``min_reps`` runs behind it is ``provisional``: one run's flag may be
noise. Its reps are the number of runs in which the no-skill arm scored it, so a single artifact
gives every item one rep. Each arm's score is the mean over the runs that scored it.

A run is one ``arms.v1`` file (or any artifact :mod:`dokimi.records` reads as an arms run), one
per repetition of the same question set. Each ``Run`` passed here counts as one run, so callers
drop repeated inputs first (the CLI does, by content digest). A question id repeated within one
run is refused: it would count as two runs. Nothing here draws a confidence interval; the paired
statistics of a comparison stay in :mod:`dokimi.eval.stats`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from dokimi.records import Record, Run

DEFAULT_CEILING = 0.9
DEFAULT_FLOOR = 0.0
DEFAULT_MIN_REPS = 3

# The smallest positive threshold: far below a score step (tenths of a point on 0-100 is 0.001), so a
# threshold this small still compares what it is given. Comparisons are exact (Fraction), so no tolerance.
_MIN_THRESHOLD = 1e-6

CEILING = "ceiling"
FLOOR = "floor"


@dataclass(frozen=True)
class ItemHeadroom:
    question_id: str
    reps: int
    provisional: bool
    no_skill: Optional[float]
    arms: Dict[str, Optional[float]]
    unscored_arms: List[str]
    flag: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "reps": self.reps,
            "provisional": self.provisional,
            "no_skill": self.no_skill,
            "arms": dict(self.arms),
            "unscored_arms": list(self.unscored_arms),
            "flag": self.flag,
        }


def check_thresholds(ceiling: float, floor: float, min_reps: int) -> None:
    """Refuse thresholds that cannot flag anything sensibly (ValueError)."""
    if not 0.0 < ceiling <= 1.0:
        raise ValueError(f"ceiling must be in (0, 1], not {ceiling!r}")
    if not 0.0 <= floor < ceiling:
        raise ValueError(f"floor must be in [0, ceiling), not {floor!r} (ceiling {ceiling!r})")
    if ceiling < _MIN_THRESHOLD:
        raise ValueError(f"ceiling must be at least {_MIN_THRESHOLD:g}, not {ceiling!r}")
    if 0.0 < floor < _MIN_THRESHOLD:
        raise ValueError(f"floor must be 0 or at least {_MIN_THRESHOLD:g}, not {floor!r}")
    if min_reps < 1:
        raise ValueError(f"min_reps must be at least 1, not {min_reps!r}")


def _mean(values: List[Fraction]) -> Optional[Fraction]:
    # Exact, not a float: a mean of 90 is exactly the 0.9 ceiling, and 89.99999999995 is exactly below it.
    return sum(values, Fraction(0)) / len(values) if values else None


def _flag(
    no_skill: Optional[Fraction], arms: Dict[str, Optional[Fraction]], control: str,
    ceiling: Fraction, floor: Fraction,
) -> Optional[str]:
    if no_skill is not None and no_skill >= ceiling:
        return CEILING
    scored = {arm: v for arm, v in arms.items() if v is not None}
    # An unscored no-skill arm cannot show the item is unanswerable: its judge may just have failed.
    if no_skill is not None and any(arm != control for arm in scored) and all(v <= floor for v in scored.values()):
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
    arms run, a question id repeated within a run, an unknown no-skill arm, no score at all for
    the no-skill arm (when there are items), or runs that disagree on the baseline when none is given.
    """
    check_thresholds(ceiling, floor, min_reps)
    control = _no_skill_arm(runs, no_skill_arm)
    scores, arms_of = _collect(runs)
    if scores and not any(control in per_arm for per_arm in scores.values()):
        # Every item would read as "no ceiling, not a floor": unmeasured, not headroom. Refuse it instead.
        raise ValueError(
            f"no run scored the no-skill arm {control!r}, so no item can be read for headroom; "
            "the runs were not judged, or the baseline is named differently (pass --baseline-arm)")

    items: List[ItemHeadroom] = []
    for qid in sorted(scores, key=_sort_key):
        per_arm = scores[qid]
        exact = {arm: _mean(per_arm.get(arm, [])) for arm in sorted(arms_of[qid])}
        arms = {arm: None if value is None else float(value) for arm, value in exact.items()}
        reps = len(per_arm.get(control, []))
        no_skill = arms.get(control)
        items.append(ItemHeadroom(
            question_id=qid,
            reps=reps,
            provisional=reps < min_reps,
            no_skill=no_skill,
            arms=arms,
            unscored_arms=[arm for arm, value in arms.items() if value is None],
            flag=_flag(exact.get(control), exact, control, Fraction(str(ceiling)), Fraction(str(floor))),
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
        raise ValueError("the runs do not declare one baseline arm; pass --baseline-arm")
    return only


def _known_arms(runs: Sequence[Run]) -> Set[str]:
    """Every arm any run declares or any record lists, ``null`` ones included."""
    known: Set[str] = set()
    for run in runs:
        known.update(run.conditions)
        for rec in run:
            known.update(_listed_arms(rec))
    return known


def _listed_arms(rec: Record) -> Set[str]:
    """The arms an arms record lists, ``null`` ones included. Unlike ``arm_names()``, keeps
    ``with_docs`` and ``without_docs``, which arms.v1 allows as arm names; a binary record lists none."""
    if rec.raw.get("arms") is None and rec.raw.get("scores") is None:
        return set()
    return set(rec.conditions)


def repeated_ids(run: Run) -> List[str]:
    """The question ids that more than one record of ``run`` carries, in first-seen order."""
    seen: Set[str] = set()
    repeated: List[str] = []
    for rec in run:
        if rec.key in seen and rec.key not in repeated:
            repeated.append(rec.key)
        seen.add(rec.key)
    return repeated


def _collect(runs: Sequence[Run]) -> Tuple[Dict[str, Dict[str, List[Fraction]]], Dict[str, Set[str]]]:
    """Question id -> arm -> the per-run scores (0-1, exact), and question id -> the arms it listed."""
    scores: Dict[str, Dict[str, List[Fraction]]] = {}
    arms_of: Dict[str, Set[str]] = {}
    for index, run in enumerate(runs, start=1):
        dupes = repeated_ids(run)
        if dupes:
            raise ValueError(
                f"run {index} lists question_id {dupes[0]!r} more than once; "
                "a run holds each question once (a repeat would count as a second run)")
        declared = set(run.conditions)  # an arm the run declares but this item lacks is unscored, not missing
        for rec in run:
            names = _listed_arms(rec) | set(rec.scores)
            arms_of.setdefault(rec.key, set()).update(names | declared)
            per_arm = scores.setdefault(rec.key, {})
            for arm in names:
                value = rec.score(arm)
                if value is not None:
                    per_arm.setdefault(arm, []).append(Fraction(str(value)) / 100)
    return scores, arms_of


def _sort_key(qid: str):
    # ASCII digits sort by value without int(), which refuses over 4300 digits: no leading zeros,
    # then length, then digits. Any other id (isdigit() also takes superscripts) sorts after them, as text.
    if qid.isascii() and qid.isdigit():
        digits = qid.lstrip("0") or "0"
        return (0, len(digits), digits, qid)
    return (1, 0, "", qid)


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
    unscored: Dict[str, int] = {}
    for item in result["items"]:
        for arm in item["unscored_arms"]:
            unscored[arm] = unscored.get(arm, 0) + 1
    if unscored:
        counts = [f"{arm} in {n} item{'' if n == 1 else 's'}" for arm, n in sorted(unscored.items())]
        lines.append("note: no run scored these arms, so the floor test leaves them out: " + "; ".join(counts))
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
    # Floored, never up, to nine decimals: a value must never read as the threshold just above it.
    # Floored from the shortest repr, not a scaled binary value, so a mean of exactly 0.9 shows 0.90.
    if value is None:
        return "-"
    text = format(Decimal(repr(float(value))).quantize(Decimal("1e-9"), rounding=ROUND_FLOOR), "f")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.rstrip('0').ljust(2, '0')}"


def _line(cells: List[str], widths: List[int]) -> str:
    return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()
