# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Analyze question quality: classify difficulty and detect trivial questions."""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from dokimi.llm import llm_call

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = """You are evaluating the quality of a technical documentation question.

Library: {library_name}
Question: {question}

Classify this question on two dimensions:

1. **Difficulty** — Choose ONE:
   - beginner: Basic usage, installation, simple how-to, terminology
   - intermediate: Integration, configuration, common patterns, troubleshooting
   - advanced: Internals, performance tuning, edge cases, architecture decisions

2. **Trivial** — Would an LLM answer this correctly WITHOUT reading the library docs?
   - true: The answer is general knowledge (e.g., "what is a thread?")
   - false: The answer requires library-specific documentation

Respond with ONLY valid JSON (no explanation):

{{"difficulty": "beginner|intermediate|advanced", "trivial": true|false, "reason": "one sentence"}}
"""


@dataclass
class QuestionClassification:
    question: str
    difficulty: str  # beginner | intermediate | advanced
    trivial: bool
    reason: str
    error: Optional[str] = None


@dataclass
class QualityReport:
    library_name: str
    total: int
    difficulty_distribution: Dict[str, int]
    trivial_count: int
    trivial_pct: float
    diversity_score: float
    questions: List[Dict[str, Any]] = field(default_factory=list)
    trivial_questions: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class QuestionQualityAnalyzer:
    """Classify questions by difficulty and triviality using an LLM judge."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
        concurrency: int = 5,
    ):
        self.model = model
        self.provider = provider
        self.concurrency = concurrency

    def classify_question(self, question: str, library_name: str) -> QuestionClassification:
        prompt = CLASSIFY_PROMPT.format(library_name=library_name, question=question)
        try:
            raw = llm_call(prompt, model=self.model, provider=self.provider)
            from dokimi.llm import extract_json_object
            data = extract_json_object(raw)
            return QuestionClassification(
                question=question,
                difficulty=data.get("difficulty", "intermediate"),
                trivial=bool(data.get("trivial", False)),
                reason=data.get("reason", ""),
            )
        except Exception as exc:
            logger.warning(f"Failed to classify question: {exc}")
            return QuestionClassification(
                question=question,
                difficulty="intermediate",
                trivial=False,
                reason="",
                error=str(exc),
            )

    def analyze(self, questions: List[str], library_name: str) -> QualityReport:
        """Classify all questions and build a quality report."""
        classifications: List[QuestionClassification] = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self.classify_question, q, library_name): q
                for q in questions
            }
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                classifications.append(result)
                logger.info(f"  [{i}/{len(questions)}] {result.difficulty} | trivial={result.trivial}")

        dist: Dict[str, int] = {"beginner": 0, "intermediate": 0, "advanced": 0}
        trivial_qs: List[str] = []
        for c in classifications:
            dist[c.difficulty] = dist.get(c.difficulty, 0) + 1
            if c.trivial:
                trivial_qs.append(c.question)

        total = len(classifications)
        trivial_count = len(trivial_qs)
        trivial_pct = round(trivial_count / total * 100, 1) if total else 0.0
        diversity_score = self._diversity_score(dist, total)
        recommendations = self._build_recommendations(dist, total, trivial_pct)

        return QualityReport(
            library_name=library_name,
            total=total,
            difficulty_distribution=dist,
            trivial_count=trivial_count,
            trivial_pct=trivial_pct,
            diversity_score=round(diversity_score, 3),
            questions=[asdict(c) for c in classifications],
            trivial_questions=trivial_qs,
            recommendations=recommendations,
        )

    @staticmethod
    def _diversity_score(dist: Dict[str, int], total: int) -> float:
        """Normalized entropy over difficulty buckets (0=all same, 1=perfectly balanced)."""
        if total == 0:
            return 0.0
        entropy = 0.0
        n_buckets = len(dist)
        for count in dist.values():
            if count > 0:
                p = count / total
                entropy -= p * math.log(p)
        max_entropy = math.log(n_buckets)
        return entropy / max_entropy if max_entropy > 0 else 0.0

    @staticmethod
    def _build_recommendations(dist: Dict[str, int], total: int, trivial_pct: float) -> List[str]:
        recs: List[str] = []
        if total == 0:
            return recs
        beginner_pct = dist.get("beginner", 0) / total * 100
        advanced_pct = dist.get("advanced", 0) / total * 100
        if trivial_pct > 20:
            recs.append(
                f"{trivial_pct:.0f}% of questions are trivial (answerable without extra context). "
                "Consider regenerating with stricter persona constraints."
            )
        if beginner_pct > 60:
            recs.append(
                "Question set is heavily skewed toward beginner difficulty. "
                "Add intermediate/advanced personas to improve coverage."
            )
        if advanced_pct < 10:
            recs.append("Very few advanced questions (<10%). Consider adding expert-level personas.")
        if not recs:
            recs.append("Question set looks well-balanced. No major issues found.")
        return recs

    def save_report(self, report: QualityReport, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        logger.info(f"Quality report saved to {path}")
