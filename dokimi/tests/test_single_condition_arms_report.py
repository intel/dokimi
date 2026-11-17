# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report eval`` on a one-arm ``arms.v1`` knowledge run shows that arm's scores, not a blank paired table."""

import json

import pytest

from dokimi.records import is_knowledge_run, is_single_condition, records
from dokimi.report.single_condition_report import _sibling_answers_path, write_eval_report


def _arms_eval(scores: dict[str, float | None], arm: str = "baseline") -> dict:
    evaluations = []
    for qid, score in scores.items():
        item = {"question_id": qid, "question_text": f"Question {qid}?", "difficulty": "easy",
                "arms": {arm: {"answer": "an answer"}},
                "scores": {arm: None if score is None else {"aggregate": score}}}
        evaluations.append(item)
    return {"judge_model": "judge", "run_metadata": {"answer_model": "m", "answer_provider": "p"},
            "evaluations": evaluations}


def test_a_one_arm_arms_run_is_one_condition_under_its_arm_name():
    recs = records(_arms_eval({"q1": 80.0, "q2": 40.0})["evaluations"], kind="evaluations")
    assert is_single_condition(recs)


def test_report_eval_on_a_one_arm_arms_run_shows_its_scores(tmp_path):
    eval_path = tmp_path / "eval" / "demo.json"
    eval_path.parent.mkdir()
    eval_path.write_text(json.dumps(_arms_eval({"q1": 80.0, "q2": 40.0, "q3": None})), encoding="utf-8")
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out))
    text = out.read_text(encoding="utf-8")
    assert "No complete pairs" not in text
    assert "| `baseline` (knowledge) | 3 | 2 | 60.0 | 40.0 | 80.0 |" in text
    assert "| q1 |" in text.split("## Questions")[1]


def test_a_two_arm_arms_run_is_not_one_condition():
    doc = _arms_eval({"q1": 80.0})
    for item in doc["evaluations"]:
        item["arms"]["skill"] = {"answer": "another"}
        item["scores"]["skill"] = {"aggregate": 70.0}
    recs = records(doc["evaluations"], kind="evaluations")
    assert not is_single_condition(recs)


def test_a_two_arm_run_whose_second_arm_failed_everywhere_is_not_one_condition():
    doc = _arms_eval({"q1": 80.0, "q2": 40.0})
    for item in doc["evaluations"]:
        item.pop("arms")  # a judged file keeps the scores, not the answers
        item["scores"]["skill"] = {"error": "judge failed: boom"}
    recs = records(doc["evaluations"], kind="evaluations")
    assert not is_single_condition(recs)


@pytest.mark.parametrize("eval_path, answers_path", [
    ("eval/demo.json", "answers/demo.json"),
    ("/work/eval/demo.json", "/work/answers/demo.json"),
    ("eval\\demo.json", "answers\\demo.json"),
    ("eval/x/eval/demo.json", "eval/x/answers/demo.json"),
])
def test_the_sibling_answers_file_swaps_the_eval_directory_not_a_substring(eval_path, answers_path):
    assert _sibling_answers_path(eval_path) == answers_path


def test_report_eval_on_a_relative_eval_path_exports_the_answer_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers").mkdir()
    (tmp_path / "eval").mkdir()
    answers = {"answers": [{"question_id": "q1", "question_text": "Q1?", "with_docs": None,
                            "without_docs": {"answer": "ANSWER TEXT", "model": "m"}}]}
    (tmp_path / "answers" / "demo.json").write_text(json.dumps(answers), encoding="utf-8")
    evaluated = {"run_metadata": {"run_intent": "knowledge", "answer_model": "m"},
                 "evaluations": [{"question_id": "q1", "question_text": "Q1?", "with_docs": None,
                                  "without_docs": {"aggregate": 80.0}}]}
    (tmp_path / "eval" / "demo.json").write_text(json.dumps(evaluated), encoding="utf-8")
    write_eval_report("eval/demo.json", "report/demo.md", "qa/demo.qa.json")
    qa = json.loads((tmp_path / "qa" / "demo.qa.json").read_text(encoding="utf-8"))
    assert qa["records"][0]["without_docs_answer"] == "ANSWER TEXT"


def test_a_declared_knowledge_run_with_nothing_scored_keeps_the_one_condition_page(tmp_path):
    # Every grade of the run is unscored (a judge-only check failed): the declared intent still decides the page.
    evaluated = {"run_metadata": {"run_intent": "knowledge", "answer_model": "m"},
                 "evaluations": [{"question_id": "q1", "question_text": "Q1?", "with_docs": None,
                                  "without_docs": {"aggregate": None}}]}
    eval_path = tmp_path / "eval" / "demo.json"
    eval_path.parent.mkdir()
    eval_path.write_text(json.dumps(evaluated), encoding="utf-8")
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out), str(tmp_path / "qa.json"))
    assert "There is no delta" in out.read_text(encoding="utf-8")


def test_a_legacy_eval_with_an_unscored_context_block_is_a_paired_run_not_a_knowledge_run():
    # Legacy reference scoring leaves the context placeholder unscored; the block still says a paired attempt.
    data = {"evaluations": [{"question_id": "q1", "question_text": "Q1?",
                             "with_docs": {"aggregate": None}, "without_docs": {"aggregate": 80.0}}]}
    recs = records(data["evaluations"], kind="evaluations")
    assert not is_knowledge_run(recs, data)
