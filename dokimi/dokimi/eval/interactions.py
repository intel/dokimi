# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Factorial (2x2) interaction contrasts between arms.

Split out of :mod:`dokimi.eval.stats` to keep that module under the
repo's 500-line ceiling; the names are re-exported from ``eval.stats`` so
existing imports keep working.

Public API
----------
detect_factorial_cells(arms)
    Find ``(a, b, a+b)`` triples among arm names, per ``CombinedTreatment``.
interaction_test(evaluations, ...)
    Properly paired 2x2 interaction contrast ``AB - A - B + baseline``.
interaction_significance(evaluations, arms, baseline_arm, ...)
    Every detected cell tested, with correction across the interaction family.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dokimi.eval.records import RecordLike, records
from dokimi.eval.stats import (
    _apply_correction,
    _describe_deltas,
    _finalize,
    _mean,
)
from dokimi.treatments.specs import split_combined_name


def detect_factorial_cells(arms: Sequence[str]) -> List[Tuple[str, str, str]]:
    """Find ``(arm_a, arm_b, combined_arm)`` triples among ``arms``.

    ``CombinedTreatment`` names itself by joining its sub-treatment names with
    ``"+"``, so ``"docs+skill:my-skill"`` is recognised as the both-factors cell
    whenever ``"docs"`` and ``"skill:my-skill"`` are also present as standalone
    arms. Only two-factor combinations are returned.

    The split is chosen by membership, not by counting separators: arm names may
    legitimately contain ``"+"`` themselves (``skill:c++-guide``), and a plain
    ``arm.split("+")`` with a ``len(parts) == 2`` guard yields four parts for
    ``docs+skill:c++-guide`` and silently reports no interaction at all. Where
    more than one split point matches, the leftmost wins — deterministic, and
    such a name would be ambiguous to a human reader too.
    """
    present = set(arms)
    cells: List[Tuple[str, str, str]] = []
    for arm in arms:
        split = split_combined_name(arm, present)
        if split is not None:
            cells.append((split[0], split[1], arm))
    return cells


def _interaction_contrast(
    evaluations: Iterable[RecordLike],
    *,
    arm_a: str,
    arm_b: str,
    combined_arm: str,
    baseline_arm: str,
    confidence: float,
    seed: int,
) -> Optional[Dict[str, Any]]:
    """Unrounded interaction result, or ``None`` when too few complete rows.

    Left unrounded so :func:`interaction_significance` can correct across the
    family before display rounding; see :func:`dokimi.eval.stats.adjust_pvalues`.
    """
    rows: List[Tuple[float, float, float, float]] = []
    for ev in records(evaluations, source="evaluations", kind="evaluations"):
        vals = [ev.score(name) for name in (combined_arm, arm_a, arm_b, baseline_arm)]
        ab, a, b, base = vals
        if ab is not None and a is not None and b is not None and base is not None:
            rows.append((float(ab), float(a), float(b), float(base)))

    if len(rows) < 2:
        return None

    contrasts = [ab - a - b + base for ab, a, b, base in rows]
    res = _describe_deltas(contrasts, confidence=confidence, seed=seed)
    res.update(
        {
            "arm_a": arm_a,
            "arm_b": arm_b,
            "combined_arm": combined_arm,
            "baseline_arm": baseline_arm,
            "delta_a": round(_mean([a - base for _, a, _, base in rows]), 2),
            "delta_b": round(_mean([b - base for _, _, b, base in rows]), 2),
            "delta_combined": round(_mean([ab - base for ab, _, _, base in rows]), 2),
            "interaction": res["delta_mean"],
        }
    )
    res["additive_expectation"] = round(res["delta_a"] + res["delta_b"], 2)
    return res


def interaction_test(
    evaluations: Iterable[RecordLike],
    *,
    arm_a: str,
    arm_b: str,
    combined_arm: str,
    baseline_arm: str,
    confidence: float = 0.95,
    seed: int = 0,
) -> Optional[Dict[str, Any]]:
    """Paired 2x2 interaction contrast for two treatments and their combination.

    The per-question contrast is ``AB - A - B + baseline``, i.e. how much the
    combined arm beats (or trails) the sum of the two individual gains. It is
    algebraically identical to ``combined_delta - (delta_a + delta_b)`` but is
    computed *within* a question against a single shared baseline, so it is a
    real paired quantity rather than a difference of separately-estimated
    means. A negative value means the treatments interfere; positive means they
    reinforce.

    Only questions where all four arms produced a score contribute. Returns
    ``None`` if fewer than two such questions exist.

    One cell in isolation carries no multiplicity, so no adjusted keys are
    reported. Use :func:`interaction_significance` when a run has several cells.
    """
    res = _interaction_contrast(
        evaluations,
        arm_a=arm_a,
        arm_b=arm_b,
        combined_arm=combined_arm,
        baseline_arm=baseline_arm,
        confidence=confidence,
        seed=seed,
    )
    if res is None:
        return None
    res.pop("p_adjusted", None)
    res.pop("significant_adjusted", None)
    return _finalize(res)


def interaction_significance(
    evaluations: Iterable[RecordLike],
    arms: Sequence[str],
    baseline_arm: str,
    *,
    correction: str = "holm",
    confidence: float = 0.95,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    """Test every factorial cell in ``arms``, corrected across the cells.

    The interaction contrasts form their own hypothesis family, separate from
    the arm-vs-baseline family: a run with three combined arms tests three
    interaction nulls, and reporting all three uncorrected next to a corrected
    arms table applies one standard to some claims and none to others. Pooling
    the two families instead would inflate the multiplier on both, since the
    contrasts answer a different question than the main effects.
    """
    evaluations = records(evaluations, source="evaluations", kind="evaluations")
    results: List[Dict[str, Any]] = []
    for arm_a, arm_b, combined in detect_factorial_cells(arms):
        res = _interaction_contrast(
            evaluations,
            arm_a=arm_a,
            arm_b=arm_b,
            combined_arm=combined,
            baseline_arm=baseline_arm,
            confidence=confidence,
            seed=seed,
        )
        if res is not None:
            results.append(res)

    _apply_correction(results, correction=correction, label="interactions")
    for res in results:
        _finalize(res)
    return results
