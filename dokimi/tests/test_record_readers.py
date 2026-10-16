# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Readers that read keys no writer writes, and malformed records, since eval/records.py.

Each eval file here comes from the writer itself (``JudgePanel.evaluate_answers``
with the LLM stubbed out), not from a hand-built dict, so a reader is held to
the keys the panel really writes: ``question``, not ``question_text``, and
``aggregate: null`` when every vote of a question failed.
"""

import argparse
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dokimi.eval.panel import JudgePanel, PanelVerdict
from dokimi.eval.records import RecordError
from tests.artifact_fixtures import artifact_fixture


def _answers():
    return artifact_fixture("answers", {"model": "m", "provider": "p", "answers": [
        {"question_id": f"q{i}", "question_text": f"How do I use feature {i}?", "library_name": "lib",
         "with_docs": {"answer": f"context answer {i}", "doc_source": "context7",
                       "retrieved_docs": [{"source": "d", "snippet": f"feature {i}", "relevance_score": 0.9}]},
         "without_docs": {"answer": f"baseline answer {i}"}}
        for i in range(3)
    ]})


def _panel_eval(failed=()):
    """A panel eval.v1 file; the questions in ``failed`` lose every vote in the context arm."""
    panel = JudgePanel.__new__(JudgePanel)
    panel.judges = [object(), object()]

    def evaluate(question, answer, library_name, context=""):
        dead = answer.startswith("context") and any(question.endswith(f" {i}?") for i in failed)
        agg = None if dead else float(60 + len(answer) % 7 + (10 if answer.startswith("context") else 0))
        return PanelVerdict(votes=[], mean_aggregate=agg, std_aggregate=None if dead else 1.0,
                            agreement_score=None if dead else 0.98)

    panel.evaluate = evaluate
    evaluations = panel.evaluate_answers(_answers()["answers"], "lib")
    return {"judge_model": "panel(2)", "library_name": "lib", "evaluations": evaluations}


# ── readers that read the wrong key ─────────────────────────────────────


def test_report_generate_shows_the_question_of_a_panel_eval_file():
    """`report generate` read `question_text`; the panel writes `question`, so every row was blank."""
    from dokimi.report.generator import ReportGenerator

    data = _panel_eval()
    assert "question_text" not in data["evaluations"][0]  # what the writer really writes
    report = json.loads(ReportGenerator().generate_report(data, {"questions": []}, "json"))
    assert [r["question_text"] for r in report["top_with_docs"]] == [
        "How do I use feature 0?", "How do I use feature 1?", "How do I use feature 2?"]


def test_report_eval_shows_the_question_of_a_panel_eval_file(tmp_path):
    """`report eval` read `question_text` too: blank table cells and a null in the Q&A sidecar."""
    from dokimi.report.eval_report import generate_report

    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_panel_eval()), encoding="utf-8")
    md, qa = tmp_path / "lib.md", tmp_path / "lib.qa.json"
    generate_report(str(path), str(md), str(qa))
    assert "| How do I use feature 1? |" in md.read_text(encoding="utf-8")
    rows = json.loads(qa.read_text(encoding="utf-8"))["records"]
    assert rows[1]["question_text"] == "How do I use feature 1?"


def test_report_generate_skips_a_question_whose_panel_votes_all_failed():
    """The panel writes `aggregate: null` for it; the report summed it and raised TypeError."""
    from dokimi.report.generator import ReportGenerator

    data = _panel_eval(failed=(1,))
    assert data["evaluations"][1]["with_docs"]["aggregate"] is None
    report = json.loads(ReportGenerator().generate_report(data, {"questions": []}, "json"))
    assert sorted(r["question_id"] for r in report["top_with_docs"]) == ["q0", "q2"]
    assert report["stats"]["total_questions"] == 3


def test_question_panel_review_reads_the_questions_of_an_answers_file(tmp_path):
    """It read `question`/`text`; answers records carry `question_text`, so it reviewed nothing."""
    from dokimi.commands.questions import cmd_questions_panel_review

    path = tmp_path / "answers.json"
    path.write_text(json.dumps(_answers()), encoding="utf-8")
    seen = []

    class Reviewer:
        def __init__(self, **_):
            pass

        def review_questions(self, questions, **_):
            seen.extend(questions)
            return SimpleNamespace(summary={}, total=len(questions))

    args = argparse.Namespace(questions=str(path), product="lib", output=str(tmp_path / "out.json"),
                              model="m", provider="openai", reviewers=None, concurrency=1, limit=None)
    with patch("dokimi.questions.panel_reviewer.QuestionPanelReviewer", Reviewer):
        cmd_questions_panel_review(args)
    assert seen == [f"How do I use feature {i}?" for i in range(3)]


def test_model_compare_reads_a_legacy_run_with_a_null_arm():
    """The legacy path called `.get` on a null arm payload and raised AttributeError."""
    from dokimi.report.model_compare import extract_scores

    def arm(score):
        return {"answer": "a", "evaluation": {"aggregate_score": score}}

    run = {"baseline_arm": "baseline", "answers": [
        {"question_id": "q1", "arms": {"baseline": arm(50), "skill": arm(70)}},
        {"question_id": "q2", "arms": {"baseline": arm(55), "skill": None}},
    ]}
    rows = extract_scores(run)
    assert [(r["question_id"], r["delta"]) for r in rows] == [("q1", 20)]


# ── malformed records are an error, not skipped ─────────────────────────


def test_the_judge_refuses_a_record_without_question_id_before_any_call():
    from dokimi.eval.judge import Judge

    judge = Judge.__new__(Judge)
    with patch.object(Judge, "_judge_answer") as call, \
         pytest.raises(RecordError, match=r"answers\[1\]: missing question_id"):
        judge.evaluate_answers("lib", [_answers()["answers"][0], {"question_text": "Q?"}])
    call.assert_not_called()


def test_the_judge_refuses_a_record_without_question_text_before_any_call():
    from dokimi.eval.judge import Judge

    judge = Judge.__new__(Judge)
    with patch.object(Judge, "_judge_answer") as call, \
         pytest.raises(RecordError, match=r"answers\[0\]: question 'q1' has no question_text"):
        judge.evaluate_answers("lib", [{"question_id": "q1", "with_docs": {"answer": "a"}}])
    call.assert_not_called()


def test_the_panel_refuses_a_record_without_question_id():
    """It used to judge it under a made-up id (`q2`)."""
    panel = JudgePanel.__new__(JudgePanel)
    panel.judges = []
    with pytest.raises(RecordError, match=r"answers\[1\]: missing question_id"):
        panel.evaluate_answers([_answers()["answers"][0], {"question_text": "Q?"}], "lib")


def test_the_degradation_pool_refuses_a_record_without_question_id():
    """It used to drop the record, and judge-validity measured the judge on fewer answers."""
    from dokimi.eval.degrade import build_pairs

    with pytest.raises(RecordError, match=r"answers\[0\]: missing question_id"):
        build_pairs([{"question_text": "Q?", "arms": {"baseline": {"answer": "x" * 2000}}}])


def test_eval_score_names_the_malformed_record(tmp_path, capsys):
    from dokimi.commands.evaluate import _load_answers

    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [{"question_text": "Q?"}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        _load_answers(str(path))
    assert "answers.json answers[0]: missing question_id" in capsys.readouterr().err


def test_the_dashboard_leaves_out_a_file_with_a_malformed_record(tmp_path, caplog):
    from dokimi.dashboard.aggregator import ResultsAggregator

    path = tmp_path / "lib.json"
    path.write_text(json.dumps({"evaluations": [{"with_docs": {"aggregate": 80}}]}), encoding="utf-8")
    assert ResultsAggregator(tmp_path)._load_snapshot(path) is None
    assert "missing question_id" in caplog.text


def test_plugin_delta_names_the_malformed_record():
    from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs

    def run(plugin_set):
        return {"schema_version": "arms.v1", "library_name": "lib", "model": "m", "provider": "p",
                "harness": "h", "arms": ["baseline"], "baseline_arm": "baseline", "total_questions": 1,
                "plugin_set": plugin_set, "answers": [{"arms": {"baseline": {"answer": "a"}}}]}

    with pytest.raises(PluginDeltaError, match=r"answers\[0\]: missing question_id"):
        compare_plugin_runs(run("none"), run("p1"))


# ── a malformed record fails cleanly and loses only itself ──────────────


def test_baseline_save_refuses_a_malformed_eval_file_before_writing(tmp_path, monkeypatch, capsys):
    from dokimi.commands import baseline

    monkeypatch.setattr(baseline, "BASELINES_DIR", tmp_path / "baselines")
    path = tmp_path / "eval.json"
    path.write_text(json.dumps({"evaluations": [
        {"question_text": "nq", "with_docs": {"aggregate": 5}, "without_docs": {"aggregate": 1}, "delta": 4}]}),
        encoding="utf-8")
    with pytest.raises(SystemExit):
        baseline.cmd_baseline_save(argparse.Namespace(from_eval=str(path), name="n1", product="P"))
    assert "evaluations[0]: missing question_id" in capsys.readouterr().err
    assert not (tmp_path / "baselines" / "n1.json").exists()


def test_report_eval_sidecar_drops_only_the_malformed_answers_record(tmp_path, capsys):
    from dokimi.report.eval_report import generate_report

    root = tmp_path / "run"
    (root / "eval").mkdir(parents=True)
    (root / "answers").mkdir()
    (root / "eval" / "lib.json").write_text(json.dumps(_panel_eval()), encoding="utf-8")
    answers = _answers()
    del answers["answers"][1]["question_id"]
    (root / "answers" / "lib.json").write_text(json.dumps(answers), encoding="utf-8")
    qa = tmp_path / "lib.qa.json"
    generate_report(str(root / "eval" / "lib.json"), str(tmp_path / "lib.md"), str(qa))
    rows = json.loads(qa.read_text(encoding="utf-8"))["records"]
    assert [r["with_docs_answer"] for r in rows] == ["context answer 0", None, "context answer 2"]
    assert "answers[1]: missing question_id" in capsys.readouterr().err


def test_stored_scores_read_an_arms_run_with_the_fallback_question_id():
    """The arm runner writes `unknown_q` for every question with no id; no join is needed."""
    from dokimi.eval import cross_judge as cj

    answers = [{"question_id": "unknown_q", "arms": {"baseline": {"answer": "b"}, "skill": {"answer": "s"}}}
               for _ in range(3)]
    evals = [{"question_id": "unknown_q", "scores": {"baseline": {"aggregate": 50}, "skill": {"aggregate": 70}}}
             for _ in range(3)]
    data = {"baseline_arm": "baseline", "answers": answers, "evaluations": evals}
    stored = cj.stored_measure(data)
    assert stored is not None
    assert stored.scores == {("unknown_q", "baseline"): 50.0, ("unknown_q", "skill"): 70.0}
    assert cj.unreproducible_arms(data) == []


def test_eval_score_exits_cleanly_on_a_record_without_question_text(tmp_path, capsys):
    from dokimi.commands.evaluate import cmd_eval_score

    path = tmp_path / "answers.json"
    path.write_text(json.dumps(_answers()), encoding="utf-8")

    class StubJudge:
        def __init__(self, **_):
            pass

        def evaluate_answers(self, *_, **__):
            raise RecordError("answers[2]: question 'q2' has no question_text")

    args = argparse.Namespace(answers=str(path), judge_provider="openai", judge_model="j", judge_temperature=0.0,
                              output=str(tmp_path / "eval.json"), product="lib", concurrency=1, run_id=None)
    with patch("dokimi.eval.Judge", StubJudge), pytest.raises(SystemExit) as exit_:
        cmd_eval_score(args)
    assert exit_.value.code == 1
    assert "answers[2]: question 'q2' has no question_text" in capsys.readouterr().err
