# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run that a run-wide error stopped says so at the top of its report (#895): the arms .md, the
one-condition page and the paired page of ``report eval``, so a shortened run is not read as a whole one."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser
from dokimi.report.arms_report import render_arms_report
from dokimi.report.single_condition_report import write_eval_report
from tests.test_knowledge_cli_answerers import _eval_doc

STOP = {"reason": "usage limit exceeded", "questions_requested": 4, "questions_recorded": 2}
NOTE = ("> **Stopped early** after 2 of 4 questions: usage limit exceeded. "
        "Every number below covers only questions answered before the stop.")


def _arms(**extra) -> dict:
    return {"library_name": "demo", "model": "m", "provider": "openai", "arms": ["baseline", "skill"],
            "baseline_arm": "baseline", "total_questions": 2, "answers": [],
            "evaluations": [{"question_id": "q1", "question_text": "How?",
                             "scores": {"baseline": {"aggregate": 60}, "skill": {"aggregate": 80}}}],
            "summary": {"per_arm": {"baseline": {"avg_aggregate": 60.0, "n": 1},
                                    "skill": {"avg_aggregate": 80.0, "delta_vs_baseline": 20.0, "n": 1}}},
            **extra}


# The arms .md of an artifact with no `terminated` block, as main renders it: the block adds the note only.
OLD_ARMS_MD = """# Treatment-arm comparison — demo

- Model: `openai/m`
- Harness: `arms-runner`
- Plugin set: `none` (`?`)
- Arms: `baseline`, `skill`
- Baseline arm: `baseline`
- Questions: 2

## Summary (avg aggregate score, 0–100)

| Arm | Avg | Δ vs baseline | n | Errors |
| --- | ---: | ---: | ---: | ---: |
| `baseline` | 60.0 | (baseline) | 1 | — |
| `skill` | 80.0 | +20.0 | 1 | — |

## Per-question scores

| Question | `baseline` | `skill` |
| --- | ---: | ---: |
| How? | 60 | 80 |
"""


def test_an_arms_artifact_without_a_terminated_block_renders_as_before():
    assert render_arms_report(_arms()) == OLD_ARMS_MD


def test_the_arms_report_of_a_stopped_run_opens_with_the_stop_and_changes_nothing_else():
    old = OLD_ARMS_MD.splitlines()
    assert render_arms_report(_arms(terminated=STOP)).splitlines() == [*old[:2], NOTE, "", *old[2:]]


def test_a_multi_line_reason_stays_inside_the_one_note_line():
    stop = {**STOP, "reason": "usage limit\nexceeded:  try again\n"}
    lines = render_arms_report(_arms(terminated=stop)).splitlines()
    assert lines[2].startswith("> **Stopped early** after 2 of 4 questions: usage limit exceeded: try again. ")
    assert lines[3] == ""


def _page(tmp_path: Path, doc: dict) -> list[str]:
    """``report eval`` on *doc*, its lines without the ``_Generated:`` stamp (the clock is not under test)."""
    eval_path = tmp_path / "eval" / "demo.json"
    eval_path.parent.mkdir(exist_ok=True)
    eval_path.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out))
    return [line for line in out.read_text(encoding="utf-8").splitlines() if not line.startswith("_Generated:")]


def _knowledge_arms_doc() -> dict:
    """A one-arm arms artifact (route B of docs/knowledge-runs.md): the block at its top level, no run_metadata."""
    doc = _eval_doc({"q1": 80.0, "q2": 40.0})
    doc.pop("run_metadata")
    return {**doc, "model": "m", "provider": "p"}


def _with_stop(doc: dict, where: str) -> dict:
    if where == "top":
        return {**doc, "terminated": STOP}
    return {**doc, "run_metadata": {**doc["run_metadata"], "terminated": STOP}}


@pytest.mark.parametrize("doc, where", [
    (_eval_doc({"q1": 80.0, "q2": 40.0}), "run_metadata"),  # an eval file of a knowledge answers file
    (_knowledge_arms_doc(), "top"),  # a one-arm arms artifact
    (_eval_doc({"q1": 80.0, "q2": 40.0}, with_context=True), "run_metadata"),  # the paired page
    (_eval_doc({"q1": 80.0, "q2": 40.0}, with_context=True), "top"),
])
def test_report_eval_opens_with_the_stop_and_changes_nothing_else(tmp_path, doc, where):
    old = _page(tmp_path, doc)
    assert not any("Stopped early" in line for line in old)
    assert _page(tmp_path, _with_stop(doc, where)) == [*old[:2], NOTE, "", *old[2:]]


def test_the_one_condition_qa_export_keeps_the_stop_of_an_arms_artifact(tmp_path):
    _page(tmp_path, _with_stop(_knowledge_arms_doc(), "top"))
    qa = json.loads((tmp_path / "report" / "demo.qa.json").read_text(encoding="utf-8"))
    assert qa["run_metadata"]["terminated"] == STOP


def _stopped_answers(tmp_path: Path) -> str:
    """An answers file that `answers generate --doc-source none` stopped after 2 of 4 questions."""
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"run_intent": "knowledge", "terminated": STOP, "answers": [
        {"question_id": f"q{i}", "question_text": f"Question {i}?", "with_docs": None,
         "without_docs": {"answer": "yes"}} for i in (1, 2)]}), encoding="utf-8")
    return str(path)


def _run(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


def test_eval_score_reference_carries_the_stop_to_the_one_condition_page(tmp_path):
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [
        {"id": f"q{i}", "question": f"Question {i}?",
         "grading": {"checks": [{"type": "match", "values": ["yes"], "location": "any"}]}} for i in (1, 2)]}))
    eval_path = tmp_path / "eval" / "demo.json"
    _run(["eval", "score", "reference", "--answers", _stopped_answers(tmp_path), "--questions", str(questions),
          "-o", str(eval_path), "--no-judge"])
    assert json.loads(eval_path.read_text())["run_metadata"]["terminated"] == STOP
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out))
    assert out.read_text(encoding="utf-8").splitlines()[3] == NOTE


def test_the_note_of_an_eval_that_graded_fewer_questions_claims_no_count_the_page_contradicts(tmp_path):
    # q2 has no grading in the questions file, so the eval holds 1 of the 2 recorded answers.
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "Question 1?", "grading": {
        "checks": [{"type": "match", "values": ["yes"], "location": "any"}]}}]}))
    eval_path = tmp_path / "eval" / "demo.json"
    _run(["eval", "score", "reference", "--answers", _stopped_answers(tmp_path), "--questions", str(questions),
          "-o", str(eval_path), "--no-judge"])
    out = tmp_path / "report" / "demo.md"
    write_eval_report(str(eval_path), str(out))
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "| Questions | 1 (1 scored) |" in lines
    assert lines[3] == NOTE


GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}')


@pytest.fixture
def judge_replies(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "completion", lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))], usage=None))


@pytest.mark.parametrize("argv", [
    ["pointwise", "--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x"],
    ["panel", "--provider", "amazon-bedrock", "--model", "us.anthropic.x", "--roles", "technical_expert"],
])
def test_a_judged_eval_of_a_stopped_answers_file_keeps_its_terminated_block(tmp_path, judge_replies, argv):
    out = tmp_path / "eval.json"
    _run(["eval", "score", argv[0], "--product", "demo", "--answers", _stopped_answers(tmp_path),
          "--output", str(out), "--concurrency", "1", *argv[1:]])
    assert json.loads(out.read_text())["run_metadata"]["terminated"] == STOP
