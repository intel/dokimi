# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Integer id ``0`` is an id everywhere a question or record id is read.

`questions.v1` allows integer ids, and ``q.get("id") or ...`` reads ``0`` as
missing: a question with id 0 was renamed, dropped, or given a fallback id
depending on which module read it. Every site now goes through
``dokimi.utils.question_id``.
"""

import pytest

from dokimi.utils import question_id


@pytest.mark.parametrize("q, expected", [
    ({"id": 0}, 0),
    ({"question_id": 0}, 0),
    ({"id": "", "question_id": "b"}, "b"),
    ({"id": None, "question_id": 0}, 0),
    ({"id": "a", "question_id": "b"}, "a"),
    ({}, None),
    ({"id": "", "question_id": ""}, None),
])
def test_question_id(q, expected):
    assert question_id(q) == expected


def test_arm_runner_reexports_the_same_helper():
    from dokimi.eval.arm_runner import question_id as from_runner

    assert from_runner is question_id


def test_normalizer_keeps_id_zero():
    from dokimi.questions.normalizer import normalize_question

    assert normalize_question({"id": 0, "question": "q"}, idx=5)["id"] == 0
    assert normalize_question({"question": "q"}, idx=5)["id"] == "q0005"


def test_degradation_pool_keeps_question_zero():
    from dokimi.eval.degrade import _answer_pool

    answer = "word " * 200
    pool = _answer_pool([{"question_id": 0, "question_text": "q?",
                          "arms": {"baseline": {"answer": answer}}}])
    assert [row["question_id"] for row in pool] == [0]


def test_model_compare_keeps_question_zero_in_both_formats():
    from dokimi.report.model_compare import extract_scores

    new = {"baseline_arm": "baseline", "evaluations": [
        {"question_id": 0, "scores": {"baseline": {"aggregate": 50},
                                       "skill": {"aggregate": 70}}}]}
    assert [s["question_id"] for s in extract_scores(new)] == [0]

    legacy = {"baseline_arm": "baseline", "answers": [
        {"question_id": 0, "arms": {
            "baseline": {"evaluation": {"aggregate_score": 50}},
            "skill": {"evaluation": {"aggregate_score": 70}}}}]}
    assert [s["question_id"] for s in extract_scores(legacy)] == [0]


def test_trigger_set_keeps_question_zero():
    from dokimi.external.trigger_set import build_trigger_set

    queries = build_trigger_set([([{"id": 0, "question": "How do I profile?"},
                                   {"question": "Another one?"}], "none")], catalog=[])
    assert [q.id for q in queries] == ["0", "q2"]


def test_checklist_keeps_question_zero(tmp_path):
    import json

    from dokimi.eval.checklist import load_points, run_checklist

    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [
        {"id": 0, "question": "q", "expected_topics": ["a point"]},
        {"id": 1, "question": "q", "expected_topics": ["a point"]}]}), encoding="utf-8")
    points = load_points(str(path))
    assert sorted(points) == [0, 1]

    records = [{"question_id": i, "question_text": "q", "arms": {"baseline": {"answer": "an answer"}}}
               for i in (0, 1)]
    report = run_checklist(records, points, lambda prompt, **kw: "YES")
    assert report.n_without_points == 0
    assert sorted(report.verdicts) == [0, 1]


def test_eval_report_accepts_integer_ids(tmp_path):
    import json

    from dokimi.report.eval_report import generate_report

    evals = [{"question_id": qid, "question_text": "q", "difficulty": "beginner",
              "with_docs": {"aggregate": 80}, "without_docs": {"aggregate": 60}, "delta": 20.0}
             for qid in (0, "a-Q001", 7)]
    src = tmp_path / "eval.json"
    src.write_text(json.dumps({"library_name": "lib", "evaluations": evals}), encoding="utf-8")
    out = tmp_path / "report.md"
    generate_report(str(src), str(out), qa_json_out=str(tmp_path / "qa.json"))

    qa = json.loads((tmp_path / "qa.json").read_text(encoding="utf-8"))
    assert [r["question_id"] for r in qa["records"]] == [0, "a-Q001", 7]
    assert "| 0 |" in out.read_text(encoding="utf-8")


def test_answerer_keeps_question_zero():
    from unittest.mock import Mock, patch

    import dokimi.eval.answerer as answerer_mod

    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    with (
        patch.object(answerer_mod, "llm_call_with_usage", return_value=("an answer", usage)),
        patch.object(answerer_mod, "ChatOpenAI", Mock, create=True),
        patch.object(answerer_mod, "LANGCHAIN_AVAILABLE", True),
    ):
        answerer = answerer_mod.Answerer()
        pair = answerer._generate_answer_pair("lib", "org/lib", {"id": 0, "question": "q?"}, 1000)
        with patch.object(answerer, "_generate_answer_pair", side_effect=RuntimeError("boom")):
            failed = answerer.generate_answers(
                "lib", "org/lib", [{"id": 0, "question": "q?"}, {"question": "q?"}])

    assert pair["question_id"] == 0
    assert [a["question_id"] for a in failed] == [0, "1"]


def test_expected_skill_error_names_question_zero():
    from dokimi.commands.arms import _validate_expected_skills

    with pytest.raises(ValueError, match="question 0: expected_skill"):
        _validate_expected_skills([{"id": 0, "question": "q?", "expected_skill": 5}], [])
    with pytest.raises(ValueError, match="question #0: expected_skill"):
        _validate_expected_skills([{"question": "q?", "expected_skill": 5}], [])
