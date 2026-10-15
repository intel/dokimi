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
