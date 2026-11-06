# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Controls that bound the novelty 2x2: placebo gating, a noise floor, atom-clustered CIs.

Each answers a reviewer question the raw ``added`` rate cannot: is the gain just context
(placebo), is it above run-to-run noise (a repeat of the baseline), and how wide is the
interval when the paraphrases of one atom are not independent (resample atoms).
"""

from __future__ import annotations

import random

# A second no-artifact answer of the same probe, sampled as a new draw. Its flips against
# the baseline are what ``added`` and ``degraded`` would be if the artifact did nothing.
REPEAT_ARM = "no_artifact_repeat"
PLACEBO_ARM = "placebo"


def _key(run) -> tuple[str, str, str]:
    return (run.atom_id, run.probe_kind, run.question)


def _usable(runs, arm: str) -> dict:
    return {_key(r): r for r in runs
            if r.arm == arm and r.probe_kind != "reversal" and not r.errored}


def clustered_ci(
    per_atom: dict[str, list[float]], *, iterations: int = 400, seed: int = 0
) -> list[float] | None:
    """Percentile bootstrap of a probe rate that resamples atoms, not probes.

    The paraphrases of one atom succeed or fail together, so a probe-level bootstrap
    reads three correlated wordings as three data points and comes out too narrow.
    """
    clusters = [values for values in per_atom.values() if values]
    if len(clusters) < 5:
        return None
    rng = random.Random(f"novelty-ci-{seed}")
    n = len(clusters)
    means = []
    for _ in range(iterations):
        picked = [clusters[rng.randrange(n)] for _ in range(n)]
        total = sum(len(c) for c in picked)
        means.append(sum(sum(c) for c in picked) / total)
    means.sort()
    lo = means[int(0.025 * iterations)]
    hi = means[min(iterations - 1, int(0.975 * iterations))]
    return [round(lo, 4), round(hi, 4)]


def placebo_gated(runs, *, baseline_arm: str, with_arm: str, scoring: str) -> dict | None:
    """``added`` that also beats the placebo: right with the artifact, wrong without it,
    and wrong with domain-mismatched text of the same length.

    ``None`` when no placebo arm ran. Only probes answered in all three arms count.
    """
    placebo = _usable(runs, PLACEBO_ARM)
    if not placebo or with_arm == PLACEBO_ARM:
        return None
    baseline, with_runs = _usable(runs, baseline_arm), _usable(runs, with_arm)
    per_atom: dict[str, list[float]] = {}
    for key, run in with_runs.items():
        before, ctrl = baseline.get(key), placebo.get(key)
        if before is None or ctrl is None:
            continue
        gated = run.correct(scoring) and not before.correct(scoring) and not ctrl.correct(scoring)
        per_atom.setdefault(run.atom_id, []).append(1.0 if gated else 0.0)
    n = sum(len(v) for v in per_atom.values())
    count = int(sum(sum(v) for v in per_atom.values()))
    return {
        "n_paired": n,
        "count": count,
        "rate": round(count / n, 4) if n else 0.0,
        "ci95": clustered_ci(per_atom),
    }


def noise_floor(runs, *, baseline_arm: str, scoring: str) -> dict | None:
    """Flip rates between two no-artifact answers of the same probe.

    ``added`` here is wrong-then-right and ``degraded`` right-then-wrong with no artifact
    at all; a with-artifact rate inside this floor is not evidence. ``None`` without the
    repeat arm.
    """
    repeat = _usable(runs, REPEAT_ARM)
    if not repeat:
        return None
    baseline = _usable(runs, baseline_arm)
    added = degraded = n = 0
    flips: dict[str, list[float]] = {}
    for key, run in repeat.items():
        before = baseline.get(key)
        if before is None:
            continue
        n += 1
        first, second = before.correct(scoring), run.correct(scoring)
        added += second and not first
        degraded += first and not second
        flips.setdefault(run.atom_id, []).append(1.0 if second and not first else 0.0)
    if not n:
        return None
    return {
        "n_paired": n,
        "draws": 1,
        "added": round(added / n, 4),
        "degraded": round(degraded / n, 4),
        "ci95_added": clustered_ci(flips),
    }


def control_fields(runs, *, baseline_arm: str, with_arm: str, scoring: str,
                   rates: dict) -> dict:
    """The control keys :func:`~dokimi.novelty.measure.paired_outcomes` adds when the
    placebo or the repeat arm ran; empty otherwise, so old outputs are unchanged."""
    fields: dict = {}
    gated = placebo_gated(runs, baseline_arm=baseline_arm, with_arm=with_arm, scoring=scoring)
    if gated is not None:
        fields["added_over_placebo"] = gated
    floor = noise_floor(runs, baseline_arm=baseline_arm, scoring=scoring)
    if floor is not None:
        fields["noise_floor"] = floor
        fields["added_net_of_noise"] = round(rates["added"] - floor["added"], 4)
        fields["degraded_within_noise"] = rates["degraded"] <= floor["degraded"]
    return fields


def _resisted(runs, arm: str) -> dict:
    return {_key(r): r.verdict != "wrong_reversed" for r in runs
            if r.arm == arm and r.probe_kind == "reversal" and not r.errored}


def reversal_by_arm(runs, arms) -> dict[str, float | None]:
    """Share of planted-inversion probes each arm refused; ``None`` for an arm with none."""
    out: dict[str, float | None] = {}
    for arm in arms:
        resisted = _resisted(runs, arm)
        out[arm] = round(sum(resisted.values()) / len(resisted), 4) if resisted else None
    return out


def reversal_paired(runs, *, baseline_arm: str, with_arm: str) -> dict | None:
    """Reversal resistance paired by probe: resisted only with the artifact, only without it.

    ``difference`` is with minus without over the pairs; ``None`` when no probe has both.
    """
    before, after = _resisted(runs, baseline_arm), _resisted(runs, with_arm)
    keys = sorted(before.keys() & after.keys())
    if not keys:
        return None
    only_with = sum(1 for k in keys if after[k] and not before[k])
    only_without = sum(1 for k in keys if before[k] and not after[k])
    both = sum(1 for k in keys if before[k] and after[k])
    return {
        "n_paired": len(keys),
        "resisted_only_with": only_with,
        "resisted_only_without": only_without,
        "resisted_both": both,
        "resisted_neither": len(keys) - only_with - only_without - both,
        "difference": round((only_with - only_without) / len(keys), 4),
    }
