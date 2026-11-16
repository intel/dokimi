# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report eval`` on a one-arm ``arms.v1`` knowledge run shows that arm's scores, not a blank paired table."""

import json

from dokimi.records import is_single_condition, records
from dokimi.report.single_condition_report import write_eval_report


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
