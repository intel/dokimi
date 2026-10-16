# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for questions/refiner.py."""

from __future__ import annotations

from dokimi.questions.refiner import QuestionRefiner, _count_difficulty


# ── Helpers ───────────────────────────────────────────────────────────────────

def _q(text: str, difficulty: str = "intermediate", idx: int = 0) -> dict:
    return {"id": f"q{idx:03d}", "question": text, "difficulty": difficulty,
            "persona": "", "category": "", "expected_topics": []}


def _qs(*pairs) -> list:
    """pairs: (text, difficulty)"""
    return [_q(t, d, i) for i, (t, d) in enumerate(pairs)]


# ── _count_difficulty ─────────────────────────────────────────────────────────

def test_count_difficulty_basic():
    questions = [
        _q("q1", "beginner"), _q("q2", "intermediate"), _q("q3", "advanced"),
        _q("q4", "intermediate"),
    ]
    counts = _count_difficulty(questions)
    assert counts["beginner"] == 1
    assert counts["intermediate"] == 2
    assert counts["advanced"] == 1


def test_count_difficulty_empty():
    assert _count_difficulty([]) == {"beginner": 0, "intermediate": 0, "advanced": 0}


# ── QuestionRefiner: trivial filter ──────────────────────────────────────────

def test_filter_trivial_removes_generic():
    refiner = QuestionRefiner("oneTBB")
    questions = [
        _q("How to use parallel_for in oneTBB?", "intermediate"),
        _q("What is a thread?", "beginner"),          # trivial keyword
        _q("Define parallelism in simple terms.", "beginner"),  # trivial "define "
    ]
    report = refiner.refine(questions)
    assert len(report.removed_trivial) == 2
    remaining_texts = [q["question"] for q in report.questions]
    assert "How to use parallel_for in oneTBB?" in remaining_texts


def test_filter_trivial_keeps_library_specific():
    refiner = QuestionRefiner("oneTBB")
    questions = [
        _q("How does oneTBB task_arena affect NUMA-aware scheduling?", "advanced"),
        _q("What is the default grain size for parallel_for in oneTBB?", "intermediate"),
    ]
    report = refiner.refine(questions)
    assert len(report.removed_trivial) == 0
    assert len(report.questions) == 2


def test_filter_trivial_case_insensitive():
    refiner = QuestionRefiner("oneTBB")
    questions = [_q("WHAT IS A THREAD?", "beginner")]
    report = refiner.refine(questions)
    assert len(report.removed_trivial) == 1


# ── QuestionRefiner: deduplication ───────────────────────────────────────────

def test_deduplication_removes_near_duplicate():
    refiner = QuestionRefiner("oneTBB", sim_threshold=0.80)
    questions = [
        _q("How do I install oneTBB on Ubuntu?", "beginner", 0),
        _q("How do I install oneTBB on Ubuntu Linux?", "beginner", 1),  # near-dup
        _q("How to configure task_arena for NUMA?", "advanced", 2),
    ]
    report = refiner.refine(questions)
    assert len(report.removed_duplicates) == 1
    assert len(report.questions) == 2


def test_deduplication_keeps_distinct():
    refiner = QuestionRefiner("oneTBB")
    questions = [
        _q("How to use parallel_for?", "intermediate", 0),
        _q("How to configure task_arena for NUMA topology?", "advanced", 1),
        _q("What are concurrent_hash_map performance considerations?", "advanced", 2),
    ]
    report = refiner.refine(questions)
    assert len(report.removed_duplicates) == 0
    assert len(report.questions) == 3


def test_deduplication_identical_removed():
    refiner = QuestionRefiner("oneTBB")
    questions = [
        _q("How to use parallel_for?", "intermediate", 0),
        _q("How to use parallel_for?", "intermediate", 1),
    ]
    report = refiner.refine(questions)
    assert len(report.removed_duplicates) == 1


# ── RefinementReport ──────────────────────────────────────────────────────────

def test_report_original_count():
    refiner = QuestionRefiner("oneTBB")
    questions = [_q(f"q{i}?", "intermediate", i) for i in range(5)]
    report = refiner.refine(questions)
    assert report.original_count == 5


def test_report_difficulty_before_after():
    refiner = QuestionRefiner("oneTBB", trivial_keywords=["trivial"])
    questions = _qs(
        ("Advanced oneTBB memory allocation internals?", "advanced"),
        ("Basic parallel_for usage?", "beginner"),
        ("trivial question?", "beginner"),  # will be removed
    )
    report = refiner.refine(questions)
    assert report.difficulty_before["beginner"] == 2
    assert report.difficulty_before["advanced"] == 1
    assert report.difficulty_after["beginner"] == 1
    assert report.difficulty_after["advanced"] == 1


def test_report_summary_contains_key_info():
    refiner = QuestionRefiner("oneTBB", target_distribution={"beginner": 5, "intermediate": 5, "advanced": 5})
    questions = [_q("oneTBB parallel_for deep dive?", "advanced", 0)]
    report = refiner.refine(questions)
    summary = report.summary()
    assert "oneTBB" in summary
    assert "beginner" in summary
    assert "target" in summary


def test_report_has_gaps_when_below_target():
    refiner = QuestionRefiner("oneTBB", target_distribution={"beginner": 10, "intermediate": 10, "advanced": 10})
    questions = [_q("oneTBB q?", "advanced", 0)]
    report = refiner.refine(questions)
    assert report.has_gaps is True


def test_report_no_gaps_when_above_target():
    target = {"beginner": 1, "intermediate": 1, "advanced": 1}
    refiner = QuestionRefiner("oneTBB", target_distribution=target)
    questions = _qs(
        ("oneTBB beginner question?", "beginner"),
        ("oneTBB intermediate question?", "intermediate"),
        ("oneTBB advanced question?", "advanced"),
    )
    report = refiner.refine(questions)
    assert report.has_gaps is False


# ── normalizer integration ────────────────────────────────────────────────────

def test_refiner_normalizes_legacy_format():
    """Legacy 'text' field and numeric difficulty should be handled transparently."""
    refiner = QuestionRefiner("oneTBB")
    legacy = [
        {"id": "q1", "text": "How to use oneTBB parallel_for?", "difficulty": 2},
        {"id": "q2", "text": "oneTBB task_arena NUMA binding explained?", "difficulty": 3},
    ]
    report = refiner.refine(legacy)
    assert len(report.questions) == 2
    assert report.questions[0]["question"] == "How to use oneTBB parallel_for?"
    assert report.questions[0]["difficulty"] == "intermediate"
    assert report.questions[1]["difficulty"] == "advanced"


def test_every_refined_question_carries_a_canonical_difficulty_label():
    """Whatever a question set puts in ``difficulty``, refine() must emit one of the three
    canonical labels — downstream reports group by that string.

    This used to load a committed product question set through a path that never existed, so
    it skipped on every run. The input is inline instead, and covers the shapes the
    normalizer has to handle: numeric, canonical, aliased, and missing.
    """
    raw = [
        {"id": "q1", "text": "How do I install it?", "difficulty": 1},
        {"id": "q2", "question": "How do I tune it?", "difficulty": "intermediate"},
        {"id": "q3", "text": "How do I debug a deadlock?", "difficulty": "hard"},
        {"id": "q4", "text": "What does the default do?"},
    ]
    report = QuestionRefiner("demo").refine(raw)

    assert report.original_count == len(raw)
    assert len(report.questions) <= report.original_count
    assert report.questions, "refine() dropped every question, so the labels are unchecked"
    for q in report.questions:
        assert q["difficulty"] in ("beginner", "intermediate", "advanced")


# ── GapFiller ─────────────────────────────────────────────────────────────────

from unittest.mock import patch
import json as _json

from dokimi.questions.refiner import GapFiller


def test_gap_filler_generates_for_missing_level():
    filler = GapFiller("oneTBB", model="gpt-6.1-sol", provider="openai")
    fake_response = _json.dumps(["How does oneTBB handle NUMA?", "Explain task_arena internals."])
    with patch("dokimi.questions.refiner.llm_call", return_value=fake_response):
        new_qs, filled = filler.fill([], gaps={"advanced": 2})
    assert filled["advanced"] == 2
    assert len(new_qs) == 2
    assert all(q["difficulty"] == "advanced" for q in new_qs)


def test_gap_filler_respects_count_limit():
    filler = GapFiller("oneTBB")
    # LLM returns 5 but we only asked for 2
    fake = _json.dumps([f"Q{i}?" for i in range(5)])
    with patch("dokimi.questions.refiner.llm_call", return_value=fake):
        new_qs, filled = filler.fill([], gaps={"beginner": 2})
    assert filled["beginner"] == 2
    assert len(new_qs) == 2


def test_gap_filler_handles_llm_error_gracefully():
    filler = GapFiller("oneTBB")
    with patch("dokimi.questions.refiner.llm_call", side_effect=RuntimeError("API down")):
        new_qs, filled = filler.fill([], gaps={"beginner": 3})
    assert filled["beginner"] == 0
    assert new_qs == []


def test_gap_filler_skips_zero_gaps():
    filler = GapFiller("oneTBB")
    with patch("dokimi.questions.refiner.llm_call") as mock_llm:
        filler.fill([], gaps={"beginner": 0, "advanced": 0})
        mock_llm.assert_not_called()


def test_refiner_fills_gaps_when_filler_provided():
    fake = _json.dumps(["oneTBB advanced Q?", "oneTBB advanced Q2?"])
    filler = GapFiller("oneTBB")
    refiner = QuestionRefiner(
        "oneTBB",
        target_distribution={"beginner": 0, "intermediate": 0, "advanced": 2},
        gap_filler=filler,
    )
    questions = [_q("oneTBB parallel_for basics?", "intermediate", 0)]
    with patch("dokimi.questions.refiner.llm_call", return_value=fake):
        report = refiner.refine(questions)
    assert report.difficulty_after["advanced"] == 2
    assert len(report.questions) == 3  # 1 original + 2 filled
