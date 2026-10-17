# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Question set refinement pipeline.

Applies post-generation quality improvements to an existing question set:

1. **Normalise** — canonical schema (text→question, 1/2/3→difficulty label).
2. **Trivial filter** — remove questions answerable without library docs.
3. **Deduplication** — remove near-duplicate questions (edit-distance based,
   no embeddings required so works without OpenAI key).
4. **Balance** — ensure target difficulty distribution; regenerate missing
   slots using the LLM if a generator is provided, otherwise just report.

Usage::

    from dokimi.questions.refiner import QuestionRefiner
    refiner = QuestionRefiner(library_name="oneTBB")
    report = refiner.refine(questions, inplace=False)
    print(report.summary())
    refined = report.questions
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from dokimi.llm import llm_call, extract_json_array
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER
from dokimi.questions.normalizer import normalize_questions

logger = logging.getLogger(__name__)

# Default target: balanced thirds
DEFAULT_TARGET = {"beginner": 10, "intermediate": 10, "advanced": 10}

# Similarity threshold: questions with ratio > this are considered duplicates
_DEFAULT_SIM_THRESHOLD = 0.82


@dataclass
class RefinementReport:
    library_name: str
    original_count: int
    questions: List[Dict[str, Any]]
    removed_trivial: List[str] = field(default_factory=list)
    removed_duplicates: List[Tuple[str, str]] = field(default_factory=list)
    difficulty_before: Dict[str, int] = field(default_factory=dict)
    difficulty_after: Dict[str, int] = field(default_factory=dict)
    target_distribution: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"── Refinement Report: {self.library_name} ──",
            f"Original:          {self.original_count} questions",
            f"Trivial removed:   {len(self.removed_trivial)}",
            f"Duplicates removed:{len(self.removed_duplicates)}",
            f"Final count:       {len(self.questions)}",
            "",
            "Difficulty distribution:",
        ]
        for level in ("beginner", "intermediate", "advanced"):
            before = self.difficulty_before.get(level, 0)
            after = self.difficulty_after.get(level, 0)
            target = self.target_distribution.get(level, "—")
            lines.append(f"  {level:<14} {before:>3} → {after:>3}  (target: {target})")

        gaps = self._gaps()
        if gaps:
            lines.append("\nGaps (need more questions):")
            for level, n in gaps.items():
                lines.append(f"  {level}: need {n} more")
        else:
            lines.append("\n✓ Distribution meets targets.")
        return "\n".join(lines)

    def _gaps(self) -> Dict[str, int]:
        gaps = {}
        for level, target in self.target_distribution.items():
            current = self.difficulty_after.get(level, 0)
            if current < target:
                gaps[level] = target - current
        return gaps

    @property
    def has_gaps(self) -> bool:
        return bool(self._gaps())


class QuestionRefiner:
    """Refine a question set: normalise → deduplicate → filter trivial → report gaps."""

    def __init__(
        self,
        library_name: str,
        target_distribution: Optional[Dict[str, int]] = None,
        sim_threshold: float = _DEFAULT_SIM_THRESHOLD,
        trivial_keywords: Optional[List[str]] = None,
        gap_filler: Optional["GapFiller"] = None,
    ):
        self.library_name = library_name
        self.target_distribution = target_distribution or dict(DEFAULT_TARGET)
        self.sim_threshold = sim_threshold
        self.gap_filler = gap_filler
        # Heuristic trivial patterns: generic CS questions not needing the docs
        self.trivial_keywords = trivial_keywords or [
            "what is a thread",
            "what is parallelism",
            "what is a mutex",
            "what is a lock",
            "what is memory",
            "what is a pointer",
            "define ",
            "what does cpu stand",
            "what is an api",
            "what is open source",
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refine(
        self,
        questions: List[Dict[str, Any]],
        before_fill: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> RefinementReport:
        """Run full refinement pipeline and return a report.

        ``before_fill`` is called with the cleaned, deduplicated set just before the gap
        filler's (paid) model calls; raising from it aborts the run with no model call.
        """
        normalized = normalize_questions(questions)
        difficulty_before = _count_difficulty(normalized)

        # Step 1: remove trivially generic questions
        filtered, trivial_removed = self._filter_trivial(normalized)
        logger.info(f"Trivial filter: removed {len(trivial_removed)} questions")

        # Step 2: deduplicate
        deduped, dup_pairs = self._deduplicate(filtered)
        logger.info(f"Deduplication: removed {len(dup_pairs)} near-duplicates")

        difficulty_after = _count_difficulty(deduped)

        # Step 3 (optional): fill gaps via LLM
        filled_questions: List[Dict[str, Any]] = []
        if self.gap_filler:
            gaps = {
                level: max(0, self.target_distribution.get(level, 0) - difficulty_after.get(level, 0))
                for level in ("beginner", "intermediate", "advanced")
            }
            gaps = {k: v for k, v in gaps.items() if v > 0}
            if gaps:
                if before_fill is not None:
                    before_fill(deduped)
                filled_questions, _ = self.gap_filler.fill(deduped, gaps)
                deduped = deduped + filled_questions
                difficulty_after = _count_difficulty(deduped)

        return RefinementReport(
            library_name=self.library_name,
            original_count=len(questions),
            questions=deduped,
            removed_trivial=trivial_removed,
            removed_duplicates=dup_pairs,
            difficulty_before=difficulty_before,
            difficulty_after=difficulty_after,
            target_distribution=dict(self.target_distribution),
        )

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _filter_trivial(
        self, questions: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Remove questions that match trivial-keyword heuristics."""
        kept: List[Dict[str, Any]] = []
        removed: List[str] = []
        for q in questions:
            text = q.get("question", "").lower()
            if any(kw in text for kw in self.trivial_keywords):
                removed.append(q.get("question", ""))
                logger.debug(f"Trivial (keyword): {q.get('question', '')[:60]}")
            else:
                kept.append(q)
        return kept, removed

    def _deduplicate(
        self, questions: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
        """Remove near-duplicate questions using SequenceMatcher ratio."""
        kept: List[Dict[str, Any]] = []
        dup_pairs: List[Tuple[str, str]] = []

        for candidate in questions:
            cand_text = candidate.get("question", "").lower()
            is_dup = False
            for existing in kept:
                existing_text = existing.get("question", "").lower()
                ratio = difflib.SequenceMatcher(
                    None, cand_text, existing_text, autojunk=False
                ).ratio()
                if ratio >= self.sim_threshold:
                    dup_pairs.append((candidate.get("question", ""), existing.get("question", "")))
                    logger.debug(
                        f"Duplicate (ratio={ratio:.2f}): "
                        f"'{candidate.get('question','')[:50]}'"
                    )
                    is_dup = True
                    break
            if not is_dup:
                kept.append(candidate)

        return kept, dup_pairs


_GAP_FILL_PROMPT = """\
You are generating technical questions for a documentation quality benchmark.

Library: __LIBRARY__
Difficulty level: __DIFFICULTY__
  - beginner: installation, basic usage, simple how-to, terminology
  - intermediate: integration, configuration, common patterns, troubleshooting
  - advanced: internals, performance tuning, edge cases, architecture decisions

Existing questions (do NOT duplicate these):
__EXISTING__

Generate exactly __COUNT__ NEW questions at the __DIFFICULTY__ level.
Requirements:
- Specific to __LIBRARY__ (not generic programming questions)
- Require reading __LIBRARY__ documentation to answer (not general knowledge)
- Distinct from the existing questions above
- Realistic (questions real developers would ask)

Respond with ONLY a JSON array of strings:
["Question 1?", "Question 2?"]
"""


class GapFiller:
    """Generate additional questions to fill difficulty distribution gaps."""

    def __init__(self, library_name: str, model: str = DEFAULT_GENERATOR_MODEL,
                 provider: str = DEFAULT_GENERATOR_PROVIDER, api_key: Optional[str] = None):
        self.library_name = library_name
        self.model = model
        self.provider = provider
        self.api_key = api_key

    def fill(
        self,
        questions: List[Dict[str, Any]],
        gaps: Dict[str, int],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Generate questions for each gap level.

        Returns:
            (new_questions, filled_counts) where filled_counts maps
            difficulty → number of questions actually generated.
        """
        new_questions: List[Dict[str, Any]] = []
        filled: Dict[str, int] = {}

        for level, count in gaps.items():
            if count <= 0:
                continue
            logger.info(f"Filling {count} {level} questions for {self.library_name}…")
            existing_texts = "\n".join(
                f"- {q.get('question', '')}"
                for q in questions
                if q.get("difficulty") == level
            ) or "(none yet)"

            prompt = (
                _GAP_FILL_PROMPT
                .replace("__LIBRARY__", self.library_name)
                .replace("__DIFFICULTY__", level)
                .replace("__COUNT__", str(count))
                .replace("__EXISTING__", existing_texts)
            )

            try:
                raw = llm_call(prompt, model=self.model, provider=self.provider,
                               api_key=self.api_key)
                texts = extract_json_array(raw)
                for i, text in enumerate(texts[:count]):
                    new_questions.append({
                        "id": f"gap_{level}_{i:03d}",
                        "question": text.strip(),
                        "difficulty": level,
                        "persona": "generated",
                        "category": "gap_fill",
                        "expected_topics": [],
                    })
                filled[level] = len(texts[:count])
                logger.info(f"  Generated {filled[level]}/{count} for {level}")
            except Exception as exc:
                logger.error(f"Gap fill failed for {level}: {exc}")
                filled[level] = 0

        return new_questions, filled


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_difficulty(questions: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {"beginner": 0, "intermediate": 0, "advanced": 0}
    for q in questions:
        level = q.get("difficulty", "intermediate")
        counts[level] = counts.get(level, 0) + 1
    return counts
