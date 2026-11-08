# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A judge panel for the paired probes: several judges grade each answer, one rule decides.

The judge-panel study (docs/judge-panel-study.md) re-graded the skill-novelty sample with
seven judges from seven families. The paired added share of one answer model moved by up to
0.23 with the judge, while the order of the extreme models did not move. Majority, median
and Dawid-Skene agreed on 99.3% of rows; unanimous and any-correct moved the headline by up
to 0.20. Three judges from three families cut the spread across panels from SD 0.039 to
0.025. Hence the defaults here: the median rule, a warning when two judges share a vendor,
and a per-judge 2×2 next to the panel's, so a judge-sensitive headline is visible.
"""

from __future__ import annotations

from collections import Counter

from .client import vendor_of

# The order the median walks: best first. The three wrong verdicts score 0 and tie.
_SCORE = {
    "correct_complete": 1.0,
    "correct_missing_qualifier": 0.7,
    "partially_correct": 0.4,
    "wrong_other": 0.0,
    "wrong_reversed": 0.0,
    "abstained": 0.0,
}
LENIENT = frozenset({"correct_complete", "correct_missing_qualifier", "partially_correct"})
RULES = ("median", "majority", "unanimous")
# A judge range of the lenient added share above this marks the headline judge-sensitive.
# The study's noise floor (a fresh no-skill draw against the first) was 0.00-0.10.
SENSITIVE_RANGE = 0.10


def _most_common(votes: list[str]) -> str:
    """The most common verdict; a tie goes to the lower score, then to the scale's order."""
    counts = Counter(votes)
    order = list(_SCORE)
    return min(counts, key=lambda v: (-counts[v], _SCORE[v], order.index(v)))


def aggregate(votes: list[str], rule: str) -> str | None:
    """One verdict from a panel's verdicts, or ``None`` when no judge returned one.

    ``votes`` holds only verdicts on the scale (failed grades are left out by the caller).

    * ``median``: the lower median of the verdict scores (an even panel rounds toward
      wrong); among the 0-score verdicts the most common one, so ``wrong_reversed`` survives.
    * ``majority``: correct (lenient) when more than half the judges say so, and then the
      median of the correct votes; otherwise the most common of the other votes.
    * ``unanimous``: correct only when every judge says so, then the lowest correct vote.
    """
    if rule not in RULES:
        raise ValueError(f"unknown judge aggregation rule {rule!r}; known: {list(RULES)}")
    if not votes:
        return None
    good = [v for v in votes if v in LENIENT]
    bad = [v for v in votes if v not in LENIENT]
    if rule == "median":
        ranked = sorted(votes, key=lambda v: -_SCORE[v])
        pick = ranked[len(ranked) // 2]
        if _SCORE[pick] == 0.0:
            return _most_common([v for v in votes if _SCORE[v] == 0.0])
        return pick
    if rule == "majority":
        if len(good) > len(votes) / 2:
            return aggregate(good, "median")
        return _most_common(bad) if bad else _most_common(good)
    # unanimous
    if not bad:
        return min(good, key=lambda v: _SCORE[v])
    return _most_common(bad)


def shared_vendors(models: list[str]) -> dict[str, list[str]]:
    """Vendor -> the panel's judges from it, for every vendor with more than one judge."""
    by_vendor: dict[str, list[str]] = {}
    for model in models:
        vendor = vendor_of(model)
        if vendor:
            by_vendor.setdefault(vendor, []).append(model)
    return {v: ms for v, ms in by_vendor.items() if len(ms) > 1}


def panel_record(measure_result, rule: str) -> dict | None:
    """The ``judge_panel`` block of a probe or novelty record: who judged, the rule, each
    judge's own paired 2×2, the judge range of the added share, and a sensitivity flag.

    ``None`` for a single-judge run, which records no panel.
    """
    from .measure import ProbeRun, paired_outcomes

    runs = measure_result.runs
    judges: list[str] = []
    for run in runs:
        for model in run.judge_verdicts or {}:
            if model not in judges:
                judges.append(model)
    if len(judges) < 2:
        return None
    per_judge: dict[str, dict] = {}
    for model in judges:
        own = [
            ProbeRun(atom_id=r.atom_id, probe_kind=r.probe_kind, arm=r.arm, question=r.question,
                     response=r.response, verdict=(r.judge_verdicts or {}).get(model, r.verdict))
            for r in runs
        ]
        per_judge[model] = {s: paired_outcomes(own, with_arm=measure_result.with_arm, scoring=s)
                            for s in ("lenient", "strict")}
    added_range: dict[str, float | None] = {}
    for scoring in ("lenient", "strict"):
        shares = []
        for row in per_judge.values():
            cell = row[scoring]
            if cell.get("n_paired"):
                shares.append(cell["added"])
        added_range[scoring] = round(max(shares) - min(shares), 4) if shares else None
    lenient_range = added_range["lenient"]
    return {
        "judges": judges,
        "rule": rule,
        "shared_vendors": shared_vendors(judges),
        "per_judge_paired": per_judge,
        "added_share_range": added_range,
        "judge_sensitive": lenient_range is not None and lenient_range > SENSITIVE_RANGE,
        "sensitive_threshold": SENSITIVE_RANGE,
    }


def panel_warnings(record: dict | None) -> list[str]:
    """Warnings a panel record carries into the artifact's ``warnings``."""
    if not record:
        return []
    out = []
    for vendor, models in sorted(record["shared_vendors"].items()):
        out.append(f"judge panel has {len(models)} judges from one vendor ({vendor}: "
                   f"{', '.join(models)}); judges of one family agree with each other more "
                   "than with the rest, so the panel counts them nearly once")
    if record["judge_sensitive"]:
        out.append(f"judge-sensitive: the lenient added share varies by "
                   f"{record['added_share_range']['lenient']:.2f} across the panel's judges "
                   f"(more than {SENSITIVE_RANGE:.2f}); report the range, not one judge's number")
    return out
