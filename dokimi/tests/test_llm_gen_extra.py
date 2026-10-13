# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Extra tests for questions/llm_gen.py — uncovered branches (generate_hybrid, save_questions)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sys

from dokimi.questions.llm_gen import QuestionGenerator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_generator():
    with patch("dokimi.questions.llm_gen.ChatOpenAI"), \
         patch("dokimi.questions.llm_gen.ChatAnthropic"):
        return QuestionGenerator(model="gpt-6.1-sol", provider="openai")


def _fake_persona(name="dev", skill="intermediate"):
    return {
        "id": "p1", "name": name, "skill_level": skill,
        "description": "A developer", "concerns": ["performance"],
    }


def _fake_question_dicts(n=3, source="chunk"):
    return [
        {"id": f"cq{i}", "question": f"Chunk question {i}?",
         "difficulty": "intermediate", "persona": "", "category": "",
         "expected_topics": [], "question_source": source}
        for i in range(n)
    ]


def _patch_chunk_gen(chunk_qs, captured_n=None):
    """Context manager: intercept the local `from chunk_gen import ...` inside generate_hybrid."""
    mock_chunk_cls = MagicMock()
    mock_instance = MagicMock()
    mock_chunk_cls.return_value = mock_instance

    def fake_generate(library_name, doc_url, n):
        if captured_n is not None:
            captured_n["n"] = n
        return MagicMock()

    mock_instance.generate.side_effect = fake_generate

    mock_module = MagicMock()
    mock_module.ChunkBasedQuestionGenerator = mock_chunk_cls
    mock_module.to_question_dicts = MagicMock(return_value=chunk_qs)

    return patch.dict(sys.modules, {"dokimi.questions.chunk_gen": mock_module})


# ── generate_hybrid ───────────────────────────────────────────────────────────

def test_generate_hybrid_merges_persona_and_chunk():
    gen = _make_generator()
    persona_q = {"text": "Persona Q?", "personas": ["p1"], "difficulty": "intermediate",
                 "topics": ["t1"], "metadata": {}}
    chunk_qs = _fake_question_dicts(3)

    with patch.object(gen, "generate_questions", return_value=[persona_q]), \
         _patch_chunk_gen(chunk_qs):
        result = gen.generate_hybrid("oneTBB", [_fake_persona()], ["parallel_for"],
                                     "https://example.com", total_questions=4)

    assert len(result) == 4
    assert all("question" in q for q in result)


def test_generate_hybrid_assigns_sequential_ids():
    gen = _make_generator()
    chunk_qs = _fake_question_dicts(4)

    with patch.object(gen, "generate_questions", return_value=[]), \
         _patch_chunk_gen(chunk_qs):
        result = gen.generate_hybrid("oneTBB", [], [], "https://x.com", total_questions=4)

    ids = [q["id"] for q in result]
    assert ids == ["q_001", "q_002", "q_003", "q_004"]


def test_generate_hybrid_persona_short_expands_chunk_budget():
    """If persona returns fewer than planned, chunk gets the remainder."""
    gen = _make_generator()
    persona_q = {"text": "Q?", "personas": ["p1"], "difficulty": "intermediate",
                 "topics": [], "metadata": {}}
    captured_n = {}
    chunk_qs = _fake_question_dicts(9)

    with patch.object(gen, "generate_questions", return_value=[persona_q]), \
         _patch_chunk_gen(chunk_qs, captured_n=captured_n):
        gen.generate_hybrid("oneTBB", [_fake_persona()], [], "https://x.com",
                            total_questions=10)

    assert captured_n.get("n", 0) >= 9


def test_generate_hybrid_sets_question_source_on_persona_qs():
    gen = _make_generator()
    persona_q = {"text": "Persona Q?", "personas": ["p1"], "difficulty": "intermediate",
                 "topics": [], "metadata": {}}

    with patch.object(gen, "generate_questions", return_value=[persona_q]), \
         _patch_chunk_gen([]):
        result = gen.generate_hybrid("oneTBB", [_fake_persona()], [], "https://x.com",
                                     total_questions=5)

    assert any(q.get("question_source") == "persona" for q in result)


def test_generate_hybrid_text_field_renamed_to_question():
    gen = _make_generator()
    persona_q = {"text": "How to use parallel_for?", "personas": ["p1"],
                 "difficulty": "advanced", "topics": [], "metadata": {}}

    with patch.object(gen, "generate_questions", return_value=[persona_q]), \
         _patch_chunk_gen([]):
        result = gen.generate_hybrid("oneTBB", [_fake_persona()], [], "https://x.com",
                                     total_questions=5)

    assert all("question" in q for q in result)
    assert all("text" not in q for q in result)


# ── save_questions ────────────────────────────────────────────────────────────

def test_save_questions_creates_file(tmp_path):
    gen = _make_generator()
    questions = [{"id": "q1", "question": "How to use TBB?", "difficulty": "intermediate"}]
    out = tmp_path / "questions.json"
    gen.save_questions(questions, out)
    assert out.exists()


def test_save_questions_correct_structure(tmp_path):
    gen = _make_generator()
    questions = [{"id": "q1", "question": "Q?"}]
    out = tmp_path / "q.json"
    gen.save_questions(questions, out)
    data = json.loads(out.read_text())
    assert data["total_questions"] == 1
    assert data["model"] == "gpt-6.1-sol"
    assert data["provider"] == "openai"
    assert len(data["questions"]) == 1
    assert "generated_at" in data


def test_save_questions_creates_parent_dirs(tmp_path):
    gen = _make_generator()
    out = tmp_path / "a" / "b" / "q.json"
    gen.save_questions([], out)
    assert out.exists()


def test_save_questions_empty_list(tmp_path):
    gen = _make_generator()
    out = tmp_path / "empty.json"
    gen.save_questions([], out)
    data = json.loads(out.read_text())
    assert data["total_questions"] == 0
    assert data["questions"] == []
