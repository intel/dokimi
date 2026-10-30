# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Trust gate — answers the question "Can we trust this run?"

Checks a set of quality signals from eval output and emits
a structured verdict with per-check pass/fail reasons.

Designed to be called from the report generator; no side-effects.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from dokimi.records import BASELINE, CONTEXT, Record, RecordLike, records


# ── Thresholds (can be overridden via spec/config) ────────────────────────────

DEFAULTS = {
    # Minimum number of evaluated questions for the run to be trusted
    "min_questions": 10,
    # Maximum fraction of questions with score == 0 (both modes)
    "max_zero_fraction": 0.20,
    # Minimum mean context-arm score (0-100)
    "min_with_docs_avg": 30.0,
    # Minimum delta (context arm minus baseline); if context doesn't help at all, suspicious
    "min_delta": -5.0,
    # Maximum coefficient of variation (std/mean) of context-arm scores — high CV = noisy
    "max_cv": 0.60,
    # Minimum inter-rater agreement fraction (when panel judge used)
    "min_agreement": 0.50,
    # Minimum fraction of questions where retrieval returned at least one doc
    "min_retrieval_hit_rate": 0.60,
    # Share of evaluations that must record a retrieval outcome (``retrieval_hit``) before
    # the hit rate is judged. Below it the rate describes a subset (e.g. 5 recorded of 20
    # reads as 100%), so the check is reported as not measured. The complement of
    # ``max_zero_fraction``: the gate already lets at most a fifth of rows be unusable.
    "min_retrieval_coverage": 0.80,
    # Maximum allowed variance across multi-run scores (std of per-run WITH averages)
    "max_multirun_std": 5.0,
}


@dataclass
class TrustCheck:
    """Result of a single trust check."""

    name: str
    passed: bool
    value: Any
    threshold: Any
    message: str
    severity: str = "warn"  # "fail" | "warn" | "info"
    #: False when the artifact does not carry the check's input. Such a check
    #: passes (it cannot block or warn) but is rendered as not measured, never as
    #: a pass — as in ``novelty_gate``.
    measured: bool = True


@dataclass
class TrustVerdict:
    """Aggregate trust verdict for a run."""

    checks: List[TrustCheck] = field(default_factory=list)
    multirun_std: Optional[float] = None

    @property
    def is_trusted(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "fail")

    @property
    def warnings(self) -> List[TrustCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "warn"]

    @property
    def failures(self) -> List[TrustCheck]:
        return [c for c in self.checks if not c.passed and c.severity == "fail"]

    @property
    def not_measured(self) -> List[str]:
        """Checks whose input this run did not produce; they pass without having been run."""
        return [c.name for c in self.checks if not c.measured]

    @property
    def status(self) -> str:
        if self.failures:
            return "❌ FAIL"
        if self.warnings:
            return "⚠️ WARN"
        return "✅ PASS"


def _retrieval_check(every: List[Record], cfg: Dict[str, Any]) -> TrustCheck:
    """The retrieval hit rate over every evaluation, ``measured=False`` when it cannot be read.

    A question "hit" retrieval when at least one document reached the
    context-arm prompt. The judge stamps that per evaluation as
    ``retrieval_hit`` (from the answer's retrieved_docs / doc_source); eval
    artifacts judged before that carry no retrieval outcome at all, and the
    check reports "not measured" for them rather than a pass. So does a mix of
    the two below ``min_retrieval_coverage``: the rate would describe a subset.
    Over every evaluation, not only the both-arms-scored ones: a question with no
    document is not judged by default, and it still records that it missed.
    """
    measured = [h for r in every if (h := r.retrieval_hit) is not None]
    n_all = len(every)
    if measured and len(measured) / n_all < cfg["min_retrieval_coverage"]:
        return TrustCheck(
            name="retrieval_hit_rate",
            passed=True,
            value=None,
            threshold=cfg["min_retrieval_hit_rate"],
            message=(
                f"Not measured: only {len(measured)} of {n_all} evaluations record a retrieval "
                f"outcome (retrieval_hit), under the {cfg['min_retrieval_coverage']:.0%} coverage "
                f"floor; {sum(measured)} of those {len(measured)} hit"
            ),
            severity="warn",
            measured=False,
        )
    if measured:
        retrieval_hits = sum(measured)
        hit_rate = retrieval_hits / len(measured)
        coverage = "" if len(measured) == n_all else f"; {n_all - len(measured)} unrecorded"
        return TrustCheck(
            name="retrieval_hit_rate",
            passed=hit_rate >= cfg["min_retrieval_hit_rate"],
            value=round(hit_rate, 3),
            threshold=cfg["min_retrieval_hit_rate"],
            message=(
                f"Retrieval hit rate = {hit_rate:.1%} ({retrieval_hits}/{len(measured)}{coverage}) "
                f"(min {cfg['min_retrieval_hit_rate']:.0%})"
            ),
            severity="warn",
        )
    return TrustCheck(
        name="retrieval_hit_rate",
        passed=True,
        value=None,
        threshold=cfg["min_retrieval_hit_rate"],
        message="Not measured: the evaluations record no retrieval outcome (retrieval_hit)",
        severity="warn",
        measured=False,
    )


def evaluate_trust(
    evaluations: Iterable[RecordLike],
    thresholds: Optional[Dict[str, Any]] = None,
    multirun_with_averages: Optional[List[float]] = None,
) -> TrustVerdict:
    """
    Compute trust verdict from a list of evaluation records.

    Args:
        evaluations:   Eval records (the `evaluations` array of an eval.v1
                       artifact, e.g. eval/{product}.json)
        thresholds:    Override any DEFAULTS key
        multirun_with_averages:  Per-run context-arm averages from N-run mode;
                                 if provided, variance check is included.
    Returns:
        TrustVerdict
    """
    cfg = {**DEFAULTS, **(thresholds or {})}
    checks: List[TrustCheck] = []

    # Parsed once: ``evaluations`` may be a one-shot iterable.
    every = records(evaluations, kind="evaluations")
    # The score checks below are over the questions scored in both arms.
    valid = [
        r for r in every
        if r.score(CONTEXT) is not None and r.score(BASELINE) is not None
    ]
    with_scores: List[float] = [r.score(CONTEXT) or 0 for r in valid]
    without_scores: List[float] = [r.score(BASELINE) or 0 for r in valid]

    # ── 1. Minimum question count ──────────────────────────────────────────
    n = len(valid)
    checks.append(TrustCheck(
        name="min_questions",
        passed=n >= cfg["min_questions"],
        value=n,
        threshold=cfg["min_questions"],
        message=f"{n} questions evaluated (min {cfg['min_questions']})",
        severity="fail",
    ))
    if n == 0:
        # Nothing is scored in both arms (every question an unjudged miss, say): the
        # retrieval rate is still known, and it is the cause, so it goes beside the count.
        if any(r.retrieval_hit is not None for r in every):
            checks.append(_retrieval_check(every, cfg))
        return TrustVerdict(checks=checks)  # can't compute anything else

    # ── 2. Zero-score fraction ─────────────────────────────────────────────
    # Misses count as unusable, judge-error misses too (by their error field): else many misses would pass.
    unjudged_misses = [
        r for r in every
        if r.score(CONTEXT) is None and r.retrieval_hit is False
        and (r.score(BASELINE) is not None or r.error is not None)
    ]
    zero_with = sum(1 for v in with_scores if v == 0) + len(unjudged_misses)
    unusable_n = n + len(unjudged_misses)
    zero_frac = zero_with / unusable_n
    checks.append(TrustCheck(
        name="zero_score_fraction",
        passed=zero_frac <= cfg["max_zero_fraction"],
        value=round(zero_frac, 3),
        threshold=cfg["max_zero_fraction"],
        message=(
            f"{zero_with}/{unusable_n} questions scored 0 or had no document in the context "
            f"arm ({zero_frac:.1%})"
        ),
        severity="fail",
    ))

    # ── 3. Minimum context-arm average ────────────────────────────────────
    with_avg = statistics.mean(with_scores)
    checks.append(TrustCheck(
        name="min_with_docs_avg",
        passed=with_avg >= cfg["min_with_docs_avg"],
        value=round(with_avg, 1),
        threshold=cfg["min_with_docs_avg"],
        message=f"Context-arm avg = {with_avg:.1f} (min {cfg['min_with_docs_avg']})",
        severity="warn",
    ))

    # ── 4. Minimum delta (context arm minus baseline) ─────────────────────
    delta_avg = statistics.mean(with_scores) - statistics.mean(without_scores)
    checks.append(TrustCheck(
        name="min_delta",
        passed=delta_avg >= cfg["min_delta"],
        value=round(delta_avg, 1),
        threshold=cfg["min_delta"],
        message=f"Avg delta (context arm minus baseline) = {delta_avg:+.1f} (min {cfg['min_delta']:+})",
        severity="warn",
    ))

    # ── 5. Score coefficient of variation (stability) ─────────────────────
    with_std = statistics.stdev(with_scores) if len(with_scores) > 1 else 0.0
    cv = (with_std / with_avg) if with_avg > 0 else 1.0
    checks.append(TrustCheck(
        name="score_cv",
        passed=cv <= cfg["max_cv"],
        value=round(cv, 3),
        threshold=cfg["max_cv"],
        message=f"Score CV = {cv:.2f} std={with_std:.1f} (max CV {cfg['max_cv']})",
        severity="warn",
    ))

    # ── 6. Inter-rater agreement (panel judge) ────────────────────────────
    agreement_scores = [a for r in valid if (a := r.scores[CONTEXT].agreement) is not None]
    if agreement_scores:
        mean_agree = statistics.mean(agreement_scores)
        checks.append(TrustCheck(
            name="inter_rater_agreement",
            passed=mean_agree >= cfg["min_agreement"],
            value=round(mean_agree, 3),
            threshold=cfg["min_agreement"],
            message=f"Mean inter-rater agreement = {mean_agree:.2f} (min {cfg['min_agreement']})",
            severity="warn",
        ))

    # ── 7. Retrieval hit rate ─────────────────────────────────────────────
    checks.append(_retrieval_check(every, cfg))

    # ── 8. Multi-run variance (optional) ─────────────────────────────────
    multirun_std = None
    if multirun_with_averages and len(multirun_with_averages) >= 2:
        multirun_std = statistics.stdev(multirun_with_averages)
        checks.append(TrustCheck(
            name="multirun_variance",
            passed=multirun_std <= cfg["max_multirun_std"],
            value=round(multirun_std, 2),
            threshold=cfg["max_multirun_std"],
            message=(
                f"Multi-run context-arm std = {multirun_std:.2f} across {len(multirun_with_averages)} runs "
                f"(max {cfg['max_multirun_std']})"
            ),
            severity="fail",
        ))

    return TrustVerdict(checks=checks, multirun_std=multirun_std)


def format_trust_block(verdict: TrustVerdict) -> str:
    """Render the '## Can We Trust This Run?' markdown block."""
    lines = [
        "## Can We Trust This Run?",
        "",
        f"**Verdict: {verdict.status}**"
        + (f" ({len(verdict.not_measured)} of {len(verdict.checks)} not measured)"
           if verdict.not_measured else ""),
        "",
        "| Check | Status | Value | Threshold | Notes |",
        "|-------|--------|-------|-----------|-------|",
    ]
    for c in verdict.checks:
        if not c.measured:
            icon = "➖"
        else:
            icon = "✅" if c.passed else ("❌" if c.severity == "fail" else "⚠️")
        lines.append(
            f"| {c.name} | {icon} | {c.value} | {c.threshold} | {c.message} |"
        )
    if verdict.failures:
        lines += ["", "**Blocking issues:**"]
        for c in verdict.failures:
            lines.append(f"- ❌ **{c.name}**: {c.message}")
    if verdict.warnings:
        lines += ["", "**Warnings:**"]
        for c in verdict.warnings:
            lines.append(f"- ⚠️ **{c.name}**: {c.message}")
    lines.append("")
    return "\n".join(lines)
