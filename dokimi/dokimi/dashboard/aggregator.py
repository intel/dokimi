# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Aggregate evaluation results from results/ directory into dashboard data."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from dokimi.records import BASELINE, CONTEXT, RecordError, records

logger = logging.getLogger(__name__)


@dataclass
class QuestionResult:
    question_id: Union[str, int]
    question: str
    with_docs_score: Optional[float]
    without_docs_score: Optional[float]
    delta: Optional[float]
    question_source: str = "persona"   # "persona" | "chunk"
    grounded: bool = False             # True if factual_grounding was scored
    # Per-dimension scores
    with_docs_dimensions: Dict[str, float] = field(default_factory=dict)
    without_docs_dimensions: Dict[str, float] = field(default_factory=dict)
    # True when the context score is the 0.0 of a retrieval miss the judge never saw (no judged score).
    # It counts in the doc health score, not in the paired means.
    unjudged_miss: bool = False


@dataclass
class ProductSnapshot:
    """Aggregated results for one product / one evaluation run."""
    product: str                       # e.g. "oneTBB"
    library_key: str                   # e.g. "onetbb"
    evaluated_at: str
    judge_model: str
    total_questions: int
    avg_with_docs: Optional[float]
    avg_without_docs: Optional[float]
    avg_delta: Optional[float]
    questions: List[QuestionResult] = field(default_factory=list)
    source_file: Optional[str] = None
    # Breakdown by question source
    by_source: Dict[str, Dict] = field(default_factory=dict)  # {"persona": {...}, "chunk": {...}}
    # Mean context score over every question, a retrieval miss counted as 0 (the doc health number).
    doc_health: Optional[float] = None

    @property
    def doc_score(self) -> Optional[float]:
        """Primary score: the doc health (or avg with_docs, or without_docs if no docs run)."""
        if self.doc_health is not None:
            return self.doc_health
        return self.avg_with_docs if self.avg_with_docs is not None else self.avg_without_docs

    @property
    def status(self) -> str:
        if self.doc_score is None:
            return "no-data"
        if self.doc_score >= 75:
            return "good"
        if self.doc_score >= 50:
            return "fair"
        return "poor"


@dataclass
class DashboardData:
    generated_at: str
    products: List[ProductSnapshot] = field(default_factory=list)

    @property
    def sorted_by_score(self) -> List[ProductSnapshot]:
        return sorted(
            self.products,
            key=lambda p: p.doc_score if p.doc_score is not None else -1,
            reverse=True,
        )


class ResultsAggregator:
    """Scan a results directory and build DashboardData."""

    def __init__(self, results_dir: Path):
        self.results_dir = results_dir

    def aggregate(self) -> DashboardData:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        snapshots = []

        if not self.results_dir.exists():
            logger.warning(f"Results directory not found: {self.results_dir}")
            return DashboardData(generated_at=now, products=[])

        # Find all eval JSON files matching orchestrator output pattern (eval/{product}.json)
        eval_files = list(self.results_dir.rglob("eval/*.json")) + \
                     list(self.results_dir.rglob("eval_*.json")) + \
                     list(self.results_dir.rglob("evaluations/*.json"))

        # Deduplicate
        seen = set()
        unique_files = []
        for f in eval_files:
            if f not in seen:
                seen.add(f)
                unique_files.append(f)

        for eval_file in sorted(unique_files):
            snapshot = self._load_snapshot(eval_file)
            if snapshot:
                snapshots.append(snapshot)

        return DashboardData(generated_at=now, products=snapshots)

    def _load_snapshot(self, path: Path) -> Optional[ProductSnapshot]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Cannot read {path}: {e}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"Invalid snapshot format in {path}: expected JSON object")
            return None

        evaluations = data.get("evaluations", [])
        if not isinstance(evaluations, list):
            logger.warning(f"Invalid evaluations format in {path}: expected list")
            return None
        if not evaluations:
            return None
        try:
            recs = records(evaluations, source=str(path), kind="evaluations")
        except RecordError as e:
            logger.warning(f"Invalid evaluation record in {path}: {e}")
            return None

        # Infer product name: prefer explicit field, fall back to filename stem
        product = data.get("library_name") or data.get("product") or path.stem
        library_key = product.lower().replace(" ", "").replace("-", "")

        def _as_float(value: Any) -> Optional[float]:
            try:
                if value is None:
                    return None
                v = float(value)
                return v if math.isfinite(v) else None
            except (TypeError, ValueError):
                return None

        questions = []

        for ev in recs:
            q_id = ev.question_id
            q_text = ev.question_text

            with_s, without_s = ev.scores.get(CONTEXT), ev.scores.get(BASELINE)
            with_eval: Mapping[str, Any] = with_s.raw if with_s is not None else {}
            without_eval: Mapping[str, Any] = without_s.raw if without_s is not None else {}

            without_score = _as_float(ev.score(BASELINE))
            judged_with = _as_float(ev.score(CONTEXT))
            with_score = _context_score(ev, with_s, judged_with, without_score)
            delta = _as_float(ev.delta)

            q_source = ev.raw.get("question_source", "persona")
            grounded = bool((with_eval or {}).get("grounded") or (without_eval or {}).get("grounded"))

            questions.append(QuestionResult(
                question_id=q_id,
                question=q_text,
                with_docs_score=with_score,
                without_docs_score=without_score,
                delta=delta,
                question_source=q_source,
                grounded=grounded,
                with_docs_dimensions={k: v for k, v in with_eval.items()
                                      if isinstance(v, (int, float)) and k != "aggregate"},
                without_docs_dimensions={k: v for k, v in without_eval.items()
                                         if isinstance(v, (int, float)) and k != "aggregate"},
                unjudged_miss=judged_with is None and with_score is not None,
            ))

        by_source = _compute_by_source(questions)
        means = _arm_means(questions)

        return ProductSnapshot(
            product=product,
            library_key=library_key,
            evaluated_at=data.get("evaluated_at", ""),
            judge_model=data.get("judge_model", "unknown"),
            total_questions=len(questions),
            avg_with_docs=means["avg_with_docs"],
            avg_without_docs=means["avg_without_docs"],
            avg_delta=means["avg_delta"],
            questions=sorted(questions, key=lambda q: (q.with_docs_score or 0)),  # worst first
            source_file=str(path),
            by_source=by_source,
            doc_health=_doc_health(questions),
        )


def _context_score(ev: Any, with_s: Any, with_score: Optional[float], without_score: Optional[float]) -> Optional[float]:
    """The context arm's score; 0.0 for a no-error retrieval miss it never answered, with a baseline score."""
    if (with_score is None and ev.retrieval_hit is False and without_score is not None
            and ev.error is None and (with_s is None or with_s.error is None)):
        return 0.0  # the docs did not help
    return with_score


def _doc_health(questions: "List[QuestionResult]") -> Optional[float]:
    """Mean context score over every question with one; a retrieval miss is its 0.0."""
    vals = [q.with_docs_score for q in questions if q.with_docs_score is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _compute_by_source(questions: "List[QuestionResult]") -> "Dict[str, Dict]":
    """Compute per-source breakdown stats (persona vs chunk)."""
    from typing import List as _L, Dict as _D
    groups: _D[str, _L] = {}
    for q in questions:
        groups.setdefault(q.question_source, []).append(q)

    result = {}
    for source, qs in groups.items():
        grounded_count = sum(1 for q in qs if q.grounded)
        result[source] = {
            "count": len(qs),
            "grounded": grounded_count,
            **_arm_means(qs),
        }
    return result


def _arm_means(questions: "List[QuestionResult]") -> "Dict[str, Optional[float]]":
    """``avg_with_docs``, ``avg_without_docs`` and ``avg_delta`` over the complete pairs.

    The questions scored in both arms, the ones :func:`dokimi.eval.stats.paired_summary`
    averages for ``report generate``, so the delta is the difference of the two means.
    An unjudged retrieval miss is no measurement (its 0.0 is only the doc health), so it
    is left out of both arms. With no pair there is no delta: an arm keeps its mean over
    its own scores when the other arm scored nothing (a baseline-only run), and otherwise
    neither mean is given, so two means over different questions never stand side by side.
    """
    measured = [q for q in questions if not q.unjudged_miss]
    with_s = [q.with_docs_score for q in measured if q.with_docs_score is not None]
    without_s = [q.without_docs_score for q in measured if q.without_docs_score is not None]
    pairs = [(q.with_docs_score, q.without_docs_score) for q in measured
             if q.with_docs_score is not None and q.without_docs_score is not None]
    if pairs:
        with_s, without_s = [w for w, _ in pairs], [wo for _, wo in pairs]
    elif with_s and without_s:
        with_s = without_s = []

    def _mean(vals: "List[float]") -> Optional[float]:
        return round(sum(vals) / len(vals), 1) if vals else None

    return {"avg_with_docs": _mean(with_s), "avg_without_docs": _mean(without_s),
            "avg_delta": _mean([w - wo for w, wo in pairs])}
