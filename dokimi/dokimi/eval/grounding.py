# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reference-free grounding / citation metrics for answer pairs.

These are cheap, deterministic, LLM-free signals that complement the RAGAS
faithfulness metric: how much of a ``with_docs`` answer is actually supported by
(grounded in) the retrieved context. Because they need no model calls, they run
in CI and can gate or contextualize the more expensive LLM-judge scores.

* ``grounding_score``: fraction of an answer's content tokens that also appear in
  the retrieved context (a coverage proxy for groundedness).
* ``citation_rate``: fraction of answers whose grounding clears a threshold.

Aggregates come with bootstrap confidence intervals so scores carry uncertainty
before being treated as blocking.
"""

from __future__ import annotations

import re
from typing import Iterable

from dokimi.eval.records import CONTEXT, RecordLike, records
from dokimi.eval.stats import bootstrap_ci

# Minimal stopword set so the overlap reflects content, not function words.
_STOPWORDS = frozenset("""
a an the and or but if then else of to in on at by for with from as is are was
were be been being this that these those it its do does did can could should
would will shall may might must not no yes you your we our they their he she
""".split())

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _content_tokens(text: str) -> set[str]:
    return {t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")) if t not in _STOPWORDS}


def grounding_score(answer: str, contexts: list[str]) -> float:
    """Fraction of the answer's content tokens present in the retrieved context."""
    a = _content_tokens(answer)
    if not a:
        return 0.0
    ctx: set[str] = set()
    for c in contexts:
        ctx |= _content_tokens(c)
    if not ctx:
        return 0.0
    return round(len(a & ctx) / len(a), 4)


def evaluate_grounding(answers: Iterable[RecordLike], threshold: float = 0.5) -> dict:
    """Compute per-question and aggregate grounding/citation metrics.

    Only ``with_docs`` answers that have retrieved context are scored.
    """
    per_question: list[dict] = []
    scores: list[float] = []
    for rec in records(answers, source="answers", kind="answers"):
        a = rec.answer(CONTEXT)
        answer = a.text if a is not None else None
        contexts = a.context if a is not None else []
        if not answer or not contexts:
            continue
        gs = grounding_score(answer, contexts)
        per_question.append({
            "question_id": rec.question_id,
            "grounding_score": gs,
            "grounded": gs >= threshold,
            "n_contexts": len(contexts),
        })
        scores.append(gs)

    n = len(per_question)
    citation_rate = round(sum(1 for p in per_question if p["grounded"]) / n, 4) if n else 0.0
    return {
        "schema_version": "grounding.v1",
        "summary": {
            "grounding_score": bootstrap_ci(scores),
            "citation_rate": citation_rate,
            "threshold": threshold,
            "n_evaluated": n,
        },
        "per_question": per_question,
    }
