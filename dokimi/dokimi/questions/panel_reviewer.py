# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-agent panel review of generated questions.

Three reviewers with distinct perspectives evaluate each question:
- domain_expert:  Is this question technically accurate and meaningful?
- user_advocate:  Would a real developer actually ask this?
- qa_engineer:    Can this question be objectively evaluated / scored?

Each reviewer scores 0-100 on their primary dimension + flags issues.
Python aggregates the scores; LLM never computes weighted average.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dokimi.llm import llm_call, extract_json_object
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER

logger = logging.getLogger(__name__)

# ── Anchors ───────────────────────────────────────────────────────────────────

_ANCHORS = """
Score anchors (0-100):
  100 = perfect for this dimension
   80 = good, minor issues
   60 = acceptable, noticeable issues
   40 = problematic, significant issues
   20 = poor, barely acceptable
    0 = completely fails this dimension
"""

# ── Role prompts ──────────────────────────────────────────────────────────────

_REVIEWER_PROMPTS: Dict[str, str] = {
    "domain_expert": """You are a DOMAIN EXPERT reviewing a documentation question about __LIBRARY__.
Your concern: Is this question technically meaningful and accurate for __LIBRARY__?

Question: "__QUESTION__"

Rate on THREE dimensions (0-100):
1. technical_accuracy — Is the question technically correct? Does it use proper terminology?
2. relevance          — Is this genuinely about __LIBRARY__ (not generic programming knowledge)?
3. depth              — Does it test something non-trivial about __LIBRARY__?

__ANCHORS__

Also flag any issues (pick all that apply, or empty list):
- "wrong_terminology" — uses incorrect technical terms
- "too_generic"       — could apply to any library, not specific to __LIBRARY__
- "trivially_googleable" — answer is a one-liner easily found without extra context
- "ambiguous"         — question can be interpreted multiple ways

Output ONLY JSON, no markdown:
{"reasoning": "one sentence", "technical_accuracy": N, "relevance": N, "depth": N, "flags": []}
""",

    "user_advocate": """You are a USER ADVOCATE reviewing a documentation question about __LIBRARY__.
Your concern: Would a real developer actually ask this question when working with __LIBRARY__?

Question: "__QUESTION__"

Rate on THREE dimensions (0-100):
1. realism       — Is this a realistic question from a real developer?
2. clarity       — Is the question clearly worded and unambiguous?
3. usefulness    — Would the answer meaningfully help the developer?

__ANCHORS__

Also flag any issues (pick all that apply, or empty list):
- "unrealistic"         — no real developer would phrase it this way
- "too_academic"        — sounds textbook-like, not from real usage
- "already_obvious"     — any experienced developer already knows the answer
- "unclear_intent"      — hard to know what kind of answer is expected

Output ONLY JSON, no markdown:
{"reasoning": "one sentence", "realism": N, "clarity": N, "usefulness": N, "flags": []}
""",

    "qa_engineer": """You are a QA ENGINEER reviewing a documentation question about __LIBRARY__.
Your concern: Can this question be objectively evaluated — is there a clear correct answer?

Question: "__QUESTION__"

Rate on THREE dimensions (0-100):
1. evaluability    — Can answers be objectively judged as correct/incorrect?
2. answerability   — Is the question answerable from documentation alone?
3. specificity     — Is it specific enough to have a single best answer?

__ANCHORS__

Also flag any issues (pick all that apply, or empty list):
- "opinion_based"     — answer depends on preference, no objective truth
- "too_broad"         — requires a book to answer properly
- "unanswerable"      — cannot be answered from __LIBRARY__ documentation alone
- "multiple_answers"  — several equally valid answers exist with no way to rank

Output ONLY JSON, no markdown:
{"reasoning": "one sentence", "evaluability": N, "answerability": N, "specificity": N, "flags": []}
""",
}

DEFAULT_REVIEWERS = ["domain_expert", "user_advocate", "qa_engineer"]

# Primary dimensions per reviewer (mean of these 3 dims = reviewer's primary_score).
# Panel score = unweighted mean across all reviewers' primary_score values.
_REVIEWER_PRIMARY_DIMS = {
    "domain_expert":  ("technical_accuracy", "relevance", "depth"),
    "user_advocate":  ("realism", "clarity", "usefulness"),
    "qa_engineer":    ("evaluability", "answerability", "specificity"),
}

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ReviewerVote:
    reviewer: str
    scores: Dict[str, float]
    reasoning: str
    flags: List[str] = field(default_factory=list)
    primary_score: Optional[float] = None   # mean of primary dims, Python-computed
    error: Optional[str] = None


@dataclass
class QuestionReview:
    question: str
    votes: List[ReviewerVote]
    panel_score: Optional[float]           # mean across all primary scores
    std: Optional[float]
    agreement_score: Optional[float]
    all_flags: List[str] = field(default_factory=list)
    recommendation: str = ""               # keep / revise / drop

    @property
    def needs_attention(self) -> bool:
        return self.panel_score is not None and (self.panel_score < 60 or bool(self.all_flags))


@dataclass
class QuestionPanelReport:
    library_name: str
    total: int
    reviewed_at: str
    questions: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


# ── Panel ─────────────────────────────────────────────────────────────────────

class QuestionPanelReviewer:
    """Run a panel of LLM reviewers on a list of questions."""

    def __init__(
        self,
        reviewers: Optional[List[str]] = None,
        model: str = DEFAULT_GENERATOR_MODEL,
        provider: str = DEFAULT_GENERATOR_PROVIDER,
        concurrency: int = 6,
    ):
        self.reviewers = reviewers or DEFAULT_REVIEWERS
        self.model = model
        self.provider = provider
        self.concurrency = concurrency

    def _build_prompt(self, reviewer: str, question: str, library_name: str) -> str:
        template = _REVIEWER_PROMPTS.get(reviewer, _REVIEWER_PROMPTS["domain_expert"])
        return (
            template
            .replace("__LIBRARY__", library_name)
            .replace("__QUESTION__", question)
            .replace("__ANCHORS__", _ANCHORS)
        )

    def _call_reviewer(self, reviewer: str, question: str, library_name: str) -> ReviewerVote:
        prompt = self._build_prompt(reviewer, question, library_name)
        try:
            raw = llm_call(prompt, model=self.model, provider=self.provider)
            data = extract_json_object(raw)
            dims = _REVIEWER_PRIMARY_DIMS[reviewer]
            # Require all dims; missing dim counts as 0 (penalize incomplete response)
            scores = {k: max(0.0, min(100.0, float(data[k]))) if k in data else 0.0 for k in dims}
            # Normalize flags: handle string (LLM bug) or list; lowercase+strip
            raw_flags = data.get("flags", [])
            if isinstance(raw_flags, list):
                flags = [f.lower().strip() for f in raw_flags if isinstance(f, str) and f.strip()]
            elif isinstance(raw_flags, str) and raw_flags.strip():
                flags = [raw_flags.lower().strip()]
            else:
                flags = []
            primary = round(statistics.mean(scores.values()), 1) if scores else None
            return ReviewerVote(
                reviewer=reviewer,
                scores=scores,
                reasoning=str(data.get("reasoning", "")),
                flags=flags,
                primary_score=primary,
            )
        except Exception as exc:
            logger.warning(f"Reviewer '{reviewer}' failed on '{question[:50]}': {exc}")
            return ReviewerVote(reviewer=reviewer, scores={}, reasoning="",
                                flags=[], error=str(exc))

    def _aggregate(self, question: str, votes: List[ReviewerVote]) -> QuestionReview:
        valid = [v for v in votes if v.primary_score is not None]
        panel_score = None
        std = None
        agreement = None
        if valid:
            scores = [v.primary_score for v in valid if v.primary_score is not None]
            panel_score = round(statistics.mean(scores), 1)
            std = round(statistics.stdev(scores), 1) if len(scores) > 1 else 0.0
            agreement = round(max(0.0, 1.0 - std / 50.0), 3)

        all_flags = sorted(set(f.lower().strip() for v in votes for f in v.flags))

        # Recommendation logic
        if panel_score is None:
            rec = "unknown"
        elif panel_score >= 70 and not all_flags:
            rec = "keep"
        elif panel_score >= 50 or (panel_score >= 40 and len(all_flags) <= 1):
            rec = "revise"
        else:
            rec = "drop"

        return QuestionReview(
            question=question,
            votes=votes,
            panel_score=panel_score,
            std=std,
            agreement_score=agreement,
            all_flags=all_flags,
            recommendation=rec,
        )

    def review_question(self, question: str, library_name: str) -> QuestionReview:
        votes: List[ReviewerVote] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._call_reviewer, r, question, library_name): r
                for r in self.reviewers
            }
            for f in as_completed(futures):
                votes.append(f.result())
        return self._aggregate(question, votes)

    def review_questions(
        self,
        questions: List[str],
        library_name: str,
        output_path: Optional[Path] = None,
        limit: Optional[int] = None,
    ) -> QuestionPanelReport:
        if limit:
            questions = questions[:limit]
        n = len(questions)
        results = []

        # Create output dir before first incremental write
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)

        for i, q in enumerate(questions, 1):
            print(f"[{i}/{n}] Reviewing: {q[:60]}…", flush=True)
            review = self.review_question(q, library_name)
            results.append(_review_to_dict(review))
            if output_path:
                _save_incremental_report(results, library_name, output_path, self)

        report = _build_report(library_name, results)
        if output_path:
            output_path.write_text(json.dumps(_report_to_dict(report), indent=2), encoding="utf-8")

        return report


# ── Helpers ───────────────────────────────────────────────────────────────────

def _review_to_dict(r: QuestionReview) -> Dict[str, Any]:
    return {
        "question": r.question,
        "panel_score": r.panel_score,
        "std": r.std,
        "agreement_score": r.agreement_score,
        "recommendation": r.recommendation,
        "flags": r.all_flags,
        "needs_attention": r.needs_attention,
        "votes": [
            {"reviewer": v.reviewer, "scores": v.scores, "primary_score": v.primary_score,
             "reasoning": v.reasoning, "flags": v.flags, "error": v.error}
            for v in r.votes
        ],
    }


def _build_report(library_name: str, results: List[Dict]) -> QuestionPanelReport:
    from datetime import datetime, timezone
    total = len(results)
    keep = sum(1 for r in results if r["recommendation"] == "keep")
    revise = sum(1 for r in results if r["recommendation"] == "revise")
    drop = sum(1 for r in results if r["recommendation"] == "drop")
    scores = [r["panel_score"] for r in results if r["panel_score"] is not None]
    flag_counts: Dict[str, int] = {}
    for r in results:
        for f in r.get("flags", []):
            flag_counts[f] = flag_counts.get(f, 0) + 1

    return QuestionPanelReport(
        library_name=library_name,
        total=total,
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        questions=results,
        summary={
            "keep": keep,
            "revise": revise,
            "drop": drop,
            "mean_panel_score": round(statistics.mean(scores), 1) if scores else None,
            "top_flags": sorted(flag_counts.items(), key=lambda x: -x[1])[:5],
        },
    )


def _report_to_dict(r: QuestionPanelReport) -> Dict[str, Any]:
    from dataclasses import asdict
    return asdict(r)


def _save_incremental_report(results, library_name, path, reviewer):
    tmp = path.with_suffix(path.suffix + ".tmp")
    out = {"library_name": library_name, "total": len(results),
           "questions": results}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, path)
