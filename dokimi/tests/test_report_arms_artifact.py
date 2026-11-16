"""``report eval`` on an arms artifact: the page and the Q&A export read the artifact's own answer model,
provider and answers, not a sibling file that an arms run never writes."""

from __future__ import annotations

import json

from tests.test_knowledge_cli_answerers import _eval_doc
from dokimi.report.single_condition_report import write_eval_report


def _arms_doc() -> dict:
    """An arms-shaped artifact of a one-arm knowledge run: the model and provider at the top level,
    the answers kept in the artifact, no run_metadata."""
    doc = _eval_doc({"q1": 80.0})
    doc.pop("run_metadata")
    doc["model"] = "gpt-4o-mini"
    doc["provider"] = "openai"
    doc["answers"] = [{"question_id": "q1", "question_text": "How do I sum an array?",
                       "without_docs": {"answer": "the saved answer"}}]
    return doc


def test_one_arm_report_names_the_answer_model_of_an_arms_artifact(tmp_path):
    eval_path = tmp_path / "eval" / "arms.json"
    eval_path.parent.mkdir()
    eval_path.write_text(json.dumps(_arms_doc()), encoding="utf-8")
    out = tmp_path / "report" / "arms.md"
    write_eval_report(str(eval_path), str(out))
    assert "| Answer model | gpt-4o-mini (openai) |" in out.read_text(encoding="utf-8")


def test_one_arm_qa_export_keeps_the_answer_text_of_an_arms_artifact_in_an_eval_directory(tmp_path):
    eval_path = tmp_path / "results" / "eval" / "arms.json"  # no sibling answers file exists
    eval_path.parent.mkdir(parents=True)
    eval_path.write_text(json.dumps(_arms_doc()), encoding="utf-8")
    out = tmp_path / "report" / "arms.md"
    qa = tmp_path / "report" / "arms.qa.json"
    write_eval_report(str(eval_path), str(out), str(qa))
    records_out = json.loads(qa.read_text(encoding="utf-8"))["records"]
    assert records_out[0]["without_docs_answer"] == "the saved answer"
    assert json.loads(qa.read_text(encoding="utf-8"))["run_metadata"]["answer_model"] == "gpt-4o-mini"
