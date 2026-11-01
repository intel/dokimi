# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Render a subject scorecard as a ``skill_evidence.v1`` document.

The scorecard is dokimi's own record of a run: every cell, rollup path and warning. The evidence
document is what a publisher hands a client next to the skill: one row per (cell, arm) with the
measured effect against the baseline, keyed by the digest a ``.well-known/agent-skills`` index
carries for the skill, so a client can match it to the skill it installed. Nothing is measured
here: every number is read from the scorecard, and a value the scorecard does not hold is null,
never filled in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from dokimi import __version__

#: The arm metric the awareness summary carries per arm (``summary.per_arm[arm]``).
METRIC = "avg_aggregate"


def build_skill_evidence(scorecard: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
    """A ``skill_evidence.v1`` payload from a ``subject_scorecard`` payload.

    ``source`` names the scorecard file it was read from; only its file name is kept, since the
    directory is the producer's disk layout and not part of the result.
    """
    subject = scorecard.get("subject") or {}
    suite = scorecard.get("suite") or {}
    warnings = list(scorecard.get("warnings") or [])
    results: list[dict[str, Any]] = []
    for cell in scorecard.get("cells") or []:
        per_arm = (cell.get("summary") or {}).get("per_arm") or {}
        baseline = _baseline_arm(cell, per_arm)
        if baseline is None:
            warnings.append(f"matrix cell {cell.get('matrix_cell')!r}: no single baseline arm, skipped")
            continue
        results.extend(_result(cell, arm, row) for arm, row in per_arm.items() if arm != baseline)
    if subject.get("distribution_digest") is None:
        warnings.append("subject has no distribution_digest (not a SKILL.md, a skill with resources, "
                        "or a non-local ref): a client cannot match this document by digest")
    if subject.get("kind") != "skill":
        warnings.append(f"subject kind is {subject.get('kind')!r}, not 'skill'")
    if (scorecard.get("work") or {}).get("status") == "run":
        warnings.append("task-suite (work) results are not in this document; only the awareness arms are")
    return {
        "schema_version": "skill_evidence.v1",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "producer": {"name": "dokimi", "version": __version__},
        "subject": {
            "id": subject.get("id"),
            "kind": subject.get("kind"),
            "ref": subject.get("ref"),
            "digest": subject.get("distribution_digest"),
            "content_digest": subject.get("ref_digest"),
        },
        "eval_set": {
            "products": list(suite.get("products") or []),
            "questions": list(suite.get("questions") or []),
            "digest": suite.get("question_set_hash"),
        },
        "baseline": scorecard.get("baseline"),
        "measured_at": scorecard.get("generated_at"),
        "source": {"kind": scorecard.get("schema_version"), "path": source and PurePath(source).name},
        "results": results,
        "warnings": sorted(set(warnings)),
    }


def _baseline_arm(cell: dict[str, Any], per_arm: dict[str, Any]) -> str | None:
    """The cell's baseline arm: as recorded, else the one arm with no delta at all, else None.

    ``delta_vs_baseline`` is also None for an arm with no paired question (every answer failed),
    so the fallback for a cell recorded before ``baseline_arm`` holds only when one arm qualifies.
    """
    if cell.get("baseline_arm") in per_arm:
        return cell["baseline_arm"]
    candidates = [name for name, row in per_arm.items()
                  if row.get("delta_vs_baseline") is None and not row.get("significance")]
    return candidates[0] if len(candidates) == 1 else None


def _result(cell: dict[str, Any], arm: str, row: dict[str, Any]) -> dict[str, Any]:
    # Every statistic is the paired test's, so n, delta, CI and p describe the same questions.
    # The per-arm means are left out: their difference is not the delta once pairs drop out.
    sig = row.get("significance") or {}
    delta = sig.get("delta_mean") if sig else None
    return {
        "product": cell.get("product"),
        "matrix_cell": cell.get("matrix_cell"),
        "model": cell.get("model"),
        "provider": cell.get("provider"),
        "harness": cell.get("harness"),
        # Absent until a run records the effort it applied; null means "not recorded", not "default".
        "effort": cell.get("effort"),
        "judge_model": cell.get("judge_model"),
        "arm": arm,
        "metric": METRIC,
        "n": sig.get("n"),
        "delta": delta,
        "ci": None if sig.get("ci_lo") is None else {
            "lo": sig.get("ci_lo"), "hi": sig.get("ci_hi"),
            "confidence": sig.get("ci_confidence"), "method": sig.get("ci_method"),
        },
        "p_value": sig.get("p_adjusted"),
        "p_correction": sig.get("correction"),
        "verdict": _verdict(delta, sig),
    }


def _verdict(delta: float | None, sig: dict[str, Any]) -> str:
    """``improved``/``regressed`` only on a corrected significant delta; else ``inconclusive``."""
    if delta is None or sig.get("significant_adjusted") is not True:  # a string "false" is not a verdict
        return "inconclusive"
    return "improved" if delta > 0 else "regressed" if delta < 0 else "inconclusive"
