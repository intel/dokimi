# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``judge_panel`` block of a probe or novelty record, built from a panel's measured runs."""

from __future__ import annotations

from .measure import BASELINE_ARM, ProbeRun, paired_outcomes
from .panel import SENSITIVE_RANGE, shared_vendors

SCORINGS = ("lenient", "strict")


def _own_runs(runs: list[ProbeRun], model: str) -> list[ProbeRun]:
    """``runs`` with each verdict replaced by the verdict ``model`` gave it."""
    return [
        ProbeRun(atom_id=r.atom_id, probe_kind=r.probe_kind, arm=r.arm, question=r.question,
                 response=r.response, verdict=(r.judge_verdicts or {}).get(model, r.verdict))
        for r in runs
    ]


def _graded_pairs(own: list[ProbeRun], with_arm: str) -> set[tuple[str, str, str]]:
    """The probes one judge graded in both the baseline and ``with_arm``."""
    def keys(arm: str) -> set[tuple[str, str, str]]:
        return {(r.atom_id, r.probe_kind, r.question) for r in own
                if r.arm == arm and r.probe_kind != "reversal" and not r.errored}
    return keys(BASELINE_ARM) & keys(with_arm)


def _added_range(per_judge_runs: dict[str, list[ProbeRun]], with_arm: str,
                 scoring: str) -> float | None:
    """The spread of the added share across judges on the pairs every judge graded.

    A judge whose grade failed on one arm of a pair drops that pair from its own 2×2; the
    range is taken on the pairs graded by all, so a missing grade is not read as a judge
    that disagrees. ``None`` when no pair is graded by every judge.
    """
    shared = set.intersection(*(_graded_pairs(own, with_arm) for own in per_judge_runs.values()))
    if not shared:
        return None
    shares = []
    for own in per_judge_runs.values():
        kept = [r for r in own
                if r.probe_kind == "reversal" or (r.atom_id, r.probe_kind, r.question) in shared]
        shares.append(paired_outcomes(kept, with_arm=with_arm, scoring=scoring)["added"])
    return round(max(shares) - min(shares), 4)


def panel_record(measure_result, rule: str) -> dict | None:
    """The ``judge_panel`` block of a probe or novelty record: who judged, the rule, each
    judge's own paired 2×2, the judge range of the added share, and a sensitivity flag.

    The range is over the pairs every judge graded in both arms; each judge's own 2×2 keeps
    its own sample (``n_unpaired_dropped`` says what it lost). ``None`` for a single-judge
    run, which records no panel.
    """
    runs = measure_result.runs
    judges: list[str] = []
    for run in runs:
        for model in run.judge_verdicts or {}:
            if model not in judges:
                judges.append(model)
    if len(judges) < 2:
        return None
    own_runs = {model: _own_runs(runs, model) for model in judges}
    with_arm = measure_result.with_arm
    per_judge = {
        model: {s: paired_outcomes(own, with_arm=with_arm, scoring=s) for s in SCORINGS}
        for model, own in own_runs.items()
    }
    added_range = {s: _added_range(own_runs, with_arm, s) for s in SCORINGS}
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
