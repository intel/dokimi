# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Normalize question records to a canonical schema.

Canonical schema:
    {
        "id":               str,
        "question":         str,   # canonical field (was "text" in questions.v1)
        "persona":          str,
        "category":         str,
        "difficulty":       str,   # "beginner" | "intermediate" | "advanced"
        "expected_topics":  list[str],
    }

This is the record shape of the questions.v2 artifact (``dokimi/schemas/``).
:func:`canonical_question` does only the v1 -> v2 key rename; :func:`normalize_question` also
relabels ``difficulty`` and fills defaults.

Usage::

    from dokimi.questions.normalizer import normalize_questions
    questions = normalize_questions(raw_list)
"""

from __future__ import annotations

from typing import Any, Dict, List

from dokimi.records import question_id

_DIFFICULTY_MAP: Dict[Any, str] = {
    1: "beginner",   "1": "beginner",  "beginner": "beginner",
    2: "intermediate", "2": "intermediate", "intermediate": "intermediate",
    3: "advanced",   "3": "advanced",  "advanced": "advanced",
    # common aliases
    "easy":   "beginner",
    "medium": "intermediate",
    "hard":   "advanced",
    "expert": "advanced",
}


def _normalize_difficulty(value: Any) -> str:
    """Map numeric or string difficulty to canonical label."""
    if value is None:
        return "intermediate"
    normalized = _DIFFICULTY_MAP.get(value)
    if normalized:
        return normalized
    # fallback: lowercase string match
    s = str(value).strip().lower()
    return _DIFFICULTY_MAP.get(s, "intermediate")


_CANONICAL_KEYS = {"id", "question", "persona", "category", "difficulty", "expected_topics"}


def question_text(q: Dict[str, Any]) -> str:
    """The question text of a record: ``question``, else the legacy ``text``, else ``""``."""
    return q.get("question") or q.get("text") or ""


def canonical_question(q: Dict[str, Any]) -> Dict[str, Any]:
    """``q`` with its text under ``question`` and no ``text`` key; every other key unchanged.

    The lossless half of :func:`normalize_question`: the questions.v1 -> questions.v2 rename,
    which writers apply before saving and readers apply to v1 files. Unlike
    :func:`normalize_question` it does not relabel ``difficulty`` or fill defaults, so a curated
    record keeps exactly what its author wrote. Returns a new dict; ``q`` is not mutated.
    """
    if "text" not in q:
        return dict(q)
    out: Dict[str, Any] = {}
    for k, v in q.items():
        if k == "question" or (k == "text" and "question" not in q):
            out["question"] = question_text(q)  # keeps the key where the text was
        elif k != "text":
            out[k] = v
    return out


def record_id(q: Dict[str, Any], idx: int) -> Any:
    """The id of the record at position ``idx`` of its set: its own, else ``q{idx:04d}``.

    Its own is ``id``, else ``question_id`` (:func:`~dokimi.records.question_id`; ``0`` is an id).
    """
    own = question_id(q)
    return f"q{idx:04d}" if own is None else own


def free_id(prefix: str, taken: set) -> str:
    """The first ``<prefix>_NNN`` not in ``taken``, added to it: a set never gives two records one id (#687)."""
    n = 0
    while f"{prefix}_{n:03d}" in taken:
        n += 1
    taken.add(f"{prefix}_{n:03d}")
    return f"{prefix}_{n:03d}"


def renumber_taken(new: List[Dict[str, Any]], kept: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``new`` with each id a ``kept`` record already has replaced by a free ``q_NNN``; ``kept`` ids stay.

    Generated ids restart at ``q_001`` on every run, so a regeneration merged with an earlier set repeats them.
    """
    kept_ids = {str(question_id(q)) for q in kept if question_id(q) is not None}
    taken = kept_ids | {str(question_id(q)) for q in new if question_id(q) is not None}
    return [{**q, "id": free_id("q", taken)} if str(question_id(q)) in kept_ids else q for q in new]


def normalize_question(q: Dict[str, Any], idx: int = 0) -> Dict[str, Any]:
    """Normalize a single question record to canonical schema.

    Extra fields (ground_truth_chunk, chunk_index, source_url, question_source,
    metadata, etc.) are preserved so chunk-grounded questions don't lose their
    ground truth reference.
    """
    canonical = {
        "id": record_id(q, idx),
        "question": question_text(q),
        "persona": q.get("persona") or "",
        "category": q.get("category") or "",
        "difficulty": _normalize_difficulty(q.get("difficulty")),
        "expected_topics": q.get("expected_topics") or [],
    }
    # Preserve all extra fields (chunk metadata, source tracking, etc.)
    for k, v in q.items():
        if k not in _CANONICAL_KEYS and k not in ("text", "question_id"):
            canonical[k] = v
    return canonical


def normalize_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize a list of question records."""
    return [normalize_question(q, i) for i, q in enumerate(questions)]
