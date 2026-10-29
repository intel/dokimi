# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Readers that read keys no writer writes, and malformed records, since dokimi/records.py.

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
from dokimi.records import RecordError
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
    from dokimi.eval.question_text import EmptyQuestionError

    judge = Judge.__new__(Judge)
    with patch.object(Judge, "_judge_answer") as call, \
         pytest.raises(EmptyQuestionError, match=r"question 'q1' has no question text"):
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
    assert exit_.value.code == 2  # bad input, as every RecordError
    assert "answers[2]: question 'q2' has no question_text" in capsys.readouterr().err


def _id_less_eval(tmp_path):
    path = tmp_path / "eval.json"
    path.write_text(json.dumps({"evaluations": [
        {"question_id": "q0", "question_text": "a", "with_docs": {"aggregate": 5},
         "without_docs": {"aggregate": 1}, "delta": 4},
        {"question_text": "b", "with_docs": {"aggregate": 5}, "without_docs": {"aggregate": 1}, "delta": 4}]}),
        encoding="utf-8")
    return path


def test_report_eval_exits_cleanly_naming_the_file(tmp_path, capsys):
    from dokimi.commands.report import cmd_report_eval

    path, out = _id_less_eval(tmp_path), tmp_path / "r.md"
    with pytest.raises(SystemExit) as exc:
        cmd_report_eval(argparse.Namespace(eval=str(path), out=str(out), product="P", run_id=None))
    assert exc.value.code == 2
    assert f"Error: {path}[1]: missing question_id" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("fmt", ["markdown", "json"])
def test_report_generate_exits_cleanly_naming_the_file(tmp_path, capsys, fmt):
    from dokimi.commands.report import cmd_report_generate

    path, out = _id_less_eval(tmp_path), tmp_path / "r.out"
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_report_generate(argparse.Namespace(eval=str(path), questions=str(questions), format=fmt,
                                               output=str(out), product="P"))
    assert exc.value.code == 2
    assert f"Error: {path} evaluations[1]: missing question_id" in capsys.readouterr().err
    assert not out.exists()


def test_model_compare_names_the_file_with_the_malformed_record(tmp_path):
    from dokimi.report.model_compare import load_run

    path = tmp_path / "run.json"
    path.write_text(json.dumps({"answers": [{"arms": {"baseline": {"answer": "a"}}}]}), encoding="utf-8")
    with pytest.raises(SystemExit, match=r"run\.json answers\[0\]: missing question_id"):
        load_run(str(path))


def test_plugin_delta_names_the_file_with_the_malformed_record(tmp_path, monkeypatch, capsys):
    import dokimi.artifacts
    from dokimi.cli import build_parser

    good = {"answers": [{"question_id": "q0", "arms": {"baseline": {"answer": "a"}}}]}
    bad = {"answers": [{"arms": {"baseline": {"answer": "a"}}}]}
    monkeypatch.setattr(dokimi.artifacts, "load_artifact", lambda kind, p: bad if p.name == "p.json" else good)
    args = build_parser().parse_args(["arms", "plugin-delta", "--baseline-json", str(tmp_path / "b.json"),
                                      "--plugin-json", str(tmp_path / "p.json"),
                                      "--out-json", str(tmp_path / "d.json")])
    with pytest.raises(SystemExit):
        args.func(args)
    assert "p.json answers[0]: missing question_id" in capsys.readouterr().err
    assert not (tmp_path / "d.json").exists()


def _ev(qid, w, wo, delta):
    return {"question_id": qid, "question_text": f"q{qid}",
            "with_docs": {"aggregate": w}, "without_docs": {"aggregate": wo}, "delta": delta}


def test_baseline_summary_leaves_out_a_question_with_an_unscored_arm():
    """A delta next to a null or non-numeric aggregate is not averaged as a 0."""
    from dokimi.commands.baseline import _compute_summary

    evals = [_ev(i, 80, 60, 20) for i in range(4)]
    evals += [_ev(4, None, 60, 20), _ev(5, "80", 60, 20)]
    summary = _compute_summary({"evaluations": evals})
    assert summary == {"n": 4, "avg_with": 80.0, "avg_without": 60.0, "avg_delta": 20.0}


def test_report_top_deltas_leave_out_a_bool_aggregate():
    """A bool aggregate is unscored; its row would raise in the markdown table."""
    from dokimi.records import records
    from dokimi.report.generator import ReportGenerator

    evals = records([_ev(0, 80, 60, 20), _ev(1, True, 60, 30)], source="e", kind="evaluations")
    gen = ReportGenerator.__new__(ReportGenerator)
    assert [t["question_id"] for t in gen._top_deltas(evals, limit=10)] == [0]
    assert [t["question_id"] for t in gen._bottom_deltas(evals, limit=10)] == [0]


def test_a_binary_record_with_a_stray_null_scores_keeps_its_scores():
    from dokimi.records import BASELINE, CONTEXT, record

    r = record({**_ev(0, 80, 60, 20), "scores": None}, kind="evaluations")
    assert (r.score(CONTEXT), r.score(BASELINE)) == (80, 60)


def test_a_binary_record_with_a_stray_null_arms_keeps_its_answers():
    """`arms: null` does not make a binary record an arms record with no conditions."""
    from dokimi.records import BASELINE, CONTEXT, record

    r = record({"question_id": 0, "question_text": "q0", "arms": None,
                "with_docs": {"answer": "with"}, "without_docs": {"answer": "without"}}, kind="answers")
    assert (r.answer(CONTEXT).text, r.answer(BASELINE).text) == ("with", "without")
    assert r.conditions == (CONTEXT, BASELINE)


def test_report_top_and_bottom_deltas_are_ordered():
    from dokimi.records import records
    from dokimi.report.generator import ReportGenerator

    evals = records([_ev(0, 70, 60, 10), _ev(1, 90, 60, 30), _ev(2, 80, 60, 20)], source="e", kind="evaluations")
    gen = ReportGenerator.__new__(ReportGenerator)
    assert [t["question_id"] for t in gen._top_deltas(evals, limit=2)] == [1, 2]
    assert [t["question_id"] for t in gen._bottom_deltas(evals, limit=2)] == [0, 2]


def test_baseline_summary_leaves_out_a_question_with_an_unscored_baseline():
    from dokimi.commands.baseline import _compute_summary

    evals = [_ev(i, 80, 60, 20) for i in range(4)] + [_ev(4, 80, None, 15)]
    summary = _compute_summary({"evaluations": evals})
    assert summary == {"n": 4, "avg_with": 80.0, "avg_without": 60.0, "avg_delta": 20.0}


# ── the arm judges read arms runs only, as before records.py ───────────


def _binary_answers_file(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [
        {"question_id": f"q{i}", "question_text": f"Q{i}?",
         "with_docs": {"answer": f"with {i}"}, "without_docs": {"answer": f"without {i}"}}
        for i in range(2)]}), encoding="utf-8")
    return path


def _legacy_arms_run():
    """An arms run from before the `evaluations` list: scores inline at arms[name].evaluation."""
    def arm(answer, score):
        return {"answer": answer, "evaluation": {"aggregate_score": score}}
    return {"library_name": "L", "baseline_arm": "baseline", "answers": [
        {"question_id": f"q{i}", "question_text": f"Q{i}?",
         "arms": {"baseline": arm(f"b{i}", 50), "skill": arm(f"s{i}", 70)}} for i in range(3)]}


def test_the_arm_judges_send_no_binary_answer_to_a_judge():
    """Before records.py these read `arms` only; with_docs/without_docs are not two arms."""
    from dokimi.eval.checklist import checklist_jobs
    from dokimi.eval.cross_judge import scored_answers
    from dokimi.eval.pairwise import pairwise_jobs

    binary = [{"question_id": "q0", "question_text": "Q0?",
               "with_docs": {"answer": "with"}, "without_docs": {"answer": "without"}}]
    arms = [{"question_id": "q1", "question_text": "Q1?",
             "arms": {"baseline": {"answer": "b"}, "skill": {"answer": "s"}}}]
    assert scored_answers(binary) == []
    assert pairwise_jobs(binary, baseline_arm="without_docs") == []
    assert checklist_jobs(binary, {"q0": ["p"]}) == []
    # A mixed list keeps its arms records, as before.
    assert [(r.question_id, arm) for r, arm, _, _ in scored_answers(binary + arms)] == [
        ("q1", "baseline"), ("q1", "skill")]
    assert [j[:2] for j in pairwise_jobs(binary + arms)] == [("skill", "q1")]


@pytest.mark.parametrize("command", [
    ["pairwise", "--baseline-arm", "without_docs"],
    ["checklist", "--baseline-arm", "without_docs"],
    ["cross-judge", "--judges", "amazon-bedrock/m", "--baseline-arm", "without_docs"],
])
def test_the_arm_judges_refuse_a_binary_answers_file_before_any_call(tmp_path, monkeypatch, capsys, command):
    """Base: no judge call and exit 2 (pairwise, checklist) or an empty table; never paid calls."""
    from unittest.mock import MagicMock

    from dokimi.cli import build_parser

    answers = _binary_answers_file(tmp_path)
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({"questions": [
        {"id": f"q{i}", "text": f"Q{i}?", "expected_topics": ["a point"]} for i in range(2)]}),
        encoding="utf-8")
    judge = MagicMock()
    monkeypatch.setattr("dokimi.eval.judge.Judge", judge)
    monkeypatch.setattr("dokimi.commands.judge_reliability.pairwise_judge", lambda args: (judge, "stub/judge"))
    extra = ["--questions", str(questions)] if command[0] == "checklist" else []
    args = build_parser().parse_args(["eval", *command, "--answers", str(answers), *extra,
                                      "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "has no arms answers" in capsys.readouterr().err
    assert judge.mock_calls == []
    assert not (tmp_path / "out.json").exists()


def test_cross_judge_takes_no_stored_measure_from_legacy_inline_scores():
    """Base read stored scores from `evaluations` only; inline legacy scores are not the stored judge."""
    from dokimi.eval.cross_judge import stored_measure, unreproducible_arms

    assert stored_measure(_legacy_arms_run()) is None
    assert unreproducible_arms(_legacy_arms_run()) == []


def test_cross_judge_takes_no_stored_measure_from_binary_evaluations():
    from dokimi.eval.cross_judge import stored_measure

    assert stored_measure({"evaluations": [_ev(0, 80, 60, 20)]}) is None


def test_cross_judge_on_a_legacy_inline_score_run_has_nothing_to_compare(tmp_path, capsys):
    """Base exit 2: no `evaluations`, so no stored measure and nothing to compare."""
    from dokimi.cli import build_parser

    path = tmp_path / "run.json"
    path.write_text(json.dumps(_legacy_arms_run()), encoding="utf-8")
    args = build_parser().parse_args(["eval", "cross-judge", "--answers", str(path),
                                      "--output", str(tmp_path / "cj.json")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "nothing to compare" in capsys.readouterr().err


def test_stored_readers_refuse_a_run_because_it_does_not_say_which_list_it_holds():
    from dokimi import records
    from dokimi.eval.cross_judge import stored_measure, unreproducible_arms

    run = records.read([{"question_id": "q1", "scores": {"skill": {"aggregate": 70}}}])
    with pytest.raises(TypeError, match="arms file's dict"):
        stored_measure(run)
    with pytest.raises(TypeError, match="arms file's dict"):
        unreproducible_arms(run)


def test_cross_judge_exits_2_on_a_malformed_arms_file(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    answers = tmp_path / "arms.json"
    answers.write_text(json.dumps({"answers": [{"arms": {"skill": {"answer": "s"}}}]}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(answers)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # bad input; 1 is the verdict code of the gates
    assert "missing question_id" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--answers", "--pairwise", "--checklist", "--validity"])
@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", "TOO-DEEP"],
                         ids=["missing", "malformed", "not-an-object", "nested-too-deep"])
def test_cross_judge_exits_2_on_an_unreadable_input_file(tmp_path, monkeypatch, capsys, flag, content):
    from dokimi import cli

    real_loads = json.loads

    def loads(text, *args, **kwargs):
        if text == "TOO-DEEP":  # whether a deep document overflows depends on the interpreter, so raise it
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads)
    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    bad = tmp_path / "bad.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    if flag == "--answers":
        argv = ["dokimi", "eval", "cross-judge", "--answers", str(bad), "--no-stored"]
    else:
        argv = ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored", flag, str(bad)]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err
    if content == "TOO-DEEP":
        assert "is not valid JSON" in err and "recursion" in err
    if content == "{not json":
        assert "is not valid JSON" in err and "cannot read" not in err
    if content is None:
        assert "cannot read" in err


@pytest.mark.parametrize("command", [["cross-judge"], ["checklist"], ["judge-wording"]],
                         ids=["cross-judge", "checklist", "judge-wording"])
@pytest.mark.parametrize("content", [None, "{not json"], ids=["missing", "malformed"])
def test_every_answers_reader_exits_2_on_an_unreadable_answers_file(tmp_path, monkeypatch, capsys, command, content):
    from dokimi import cli

    bad = tmp_path / "bad.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": []}), encoding="utf-8")
    extra = ["--questions", str(questions)] if command[0] == "checklist" else []
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", command[0], "--answers", str(bad), *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


@pytest.mark.parametrize("flag", ["--pairwise", "--checklist", "--validity"])
def test_cross_judge_refuses_a_missing_result_file_before_any_judge_call(tmp_path, monkeypatch, capsys, flag):
    from tests.test_judge_reliability import _cross_judge_args, _material_arms_run, _recording_judge

    seen: dict = {}
    _recording_judge(monkeypatch, seen)
    missing = tmp_path / "missing.json"
    args = _cross_judge_args(tmp_path, _material_arms_run(), "--no-stored", "--judges", "amazon-bedrock/m",
                             flag, str(missing))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2 and str(missing) in capsys.readouterr().err
    assert seen == {}  # no paid judge call was made


@pytest.mark.parametrize("content", [{"x": 1}, {"summary": []}], ids=["no-summary", "summary-not-an-object"])
def test_cross_judge_exits_2_on_a_validity_file_without_a_summary_object(tmp_path, monkeypatch, capsys, content):
    from dokimi import cli

    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    validity = tmp_path / "validity.json"
    validity.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored",
                                     "--validity", str(validity)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(validity) in err and "`summary`" in err


@pytest.mark.parametrize("flag, content", [
    ("--pairwise", {}), ("--pairwise", {"questions": []}),
    ("--checklist", {}), ("--checklist", {"verdicts": {}}),
], ids=["pairwise-empty", "pairwise-is-a-checklist",
        "checklist-empty", "checklist-is-a-pairwise"])
def test_cross_judge_exits_2_on_a_result_file_of_the_wrong_shape(tmp_path, monkeypatch, capsys, flag, content):
    from dokimi import cli

    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored",
                                     flag, str(wrong)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(wrong) in err


_PAIRWISE = {"verdicts": {"skill": {"q1": {"score": 1}}}}
_CHECKLIST = {"questions": [{"question_id": "q1", "arms": {"skill": {"coverage": 1}}}]}


@pytest.mark.parametrize("flag, doc", [("--pairwise", _PAIRWISE), ("--checklist", _CHECKLIST)])
@pytest.mark.parametrize("summary", [None, [], "x", {}, {"judge": None}, {"judge": 5}, {"judge": ""}],
                         ids=["absent", "list", "string", "no-judge", "null-judge", "int-judge", "empty-judge"])
def test_cross_judge_names_a_result_file_measure_after_its_stem_without_a_usable_judge(
        tmp_path, monkeypatch, flag, doc, summary):
    from dokimi import cli

    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    result = tmp_path / "result.json"
    result.write_text(json.dumps({**doc, **({} if summary is None else {"summary": summary})}), encoding="utf-8")
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored",
                                     flag, str(result), "--output", str(out)])
    cli.main()
    assert " · result |" in out.with_suffix(".md").read_text(encoding="utf-8")  # named after result.json


@pytest.mark.parametrize("flag, doc", [("--pairwise", _PAIRWISE), ("--checklist", _CHECKLIST)])
def test_cross_judge_names_a_result_file_measure_after_its_summary_judge(tmp_path, monkeypatch, flag, doc):
    from dokimi import cli

    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    result = tmp_path / "result.json"
    result.write_text(json.dumps({**doc, "summary": {"judge": "judge-a"}}), encoding="utf-8")
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored",
                                     flag, str(result), "--output", str(out)])
    cli.main()
    md = out.with_suffix(".md").read_text(encoding="utf-8")
    assert " · judge-a |" in md and " · result |" not in md


_GOOD_QUESTION = {"question_id": "q0", "arms": {"skill": {"coverage": 1}}}


def _pairwise(verdict):
    return {"verdicts": {"skill": {"q1": verdict}}}


def _checklist(question):
    return {"questions": [question]}


@pytest.mark.parametrize("flag, content, where", [
    pytest.param("--pairwise", _pairwise({}), "verdicts.skill.q1", id="no-score"),
    pytest.param("--pairwise", _pairwise(5), "verdicts.skill.q1", id="verdict-not-an-object"),
    pytest.param("--pairwise", _pairwise({"score": "x"}), "verdicts.skill.q1", id="score-not-a-number"),
    pytest.param("--pairwise", _pairwise({"score": "1"}), "verdicts.skill.q1", id="score-a-numeric-string"),
    pytest.param("--pairwise", _pairwise({"score": None}), "verdicts.skill.q1", id="null-score"),
    pytest.param("--pairwise", _pairwise({"score": True}), "verdicts.skill.q1", id="bool-score"),
    pytest.param("--pairwise", _pairwise({"score": float("nan")}), "verdicts.skill.q1", id="nan-score"),
    pytest.param("--pairwise", _pairwise({"score": float("inf")}), "verdicts.skill.q1", id="infinite-score"),
    pytest.param("--pairwise", _pairwise({"score": 10 ** 400}), "verdicts.skill.q1", id="score-past-float-range"),
    pytest.param("--pairwise", {"verdicts": {"skill": 5}}, "verdicts.skill", id="arm-not-an-object"),
    pytest.param("--checklist", {"questions": [5]}, "questions[0]", id="question-not-an-object"),
    pytest.param("--checklist", {"questions": [_GOOD_QUESTION, {"arms": {"skill": {"coverage": 1}}}]},
                 "questions[1]", id="no-question-id"),
    pytest.param("--checklist", _checklist({"question_id": None, "arms": {"skill": {"coverage": 1}}}),
                 "questions[0]", id="null-question-id"),
    pytest.param("--checklist", _checklist({"question_id": True, "arms": {"skill": {"coverage": 1}}}),
                 "questions[0]", id="bool-question-id"),
    pytest.param("--checklist", _checklist({"question_id": ["q1"], "arms": {"skill": {"coverage": 1}}}),
                 "questions[0]", id="unhashable-question-id"),
    pytest.param("--checklist", _checklist({"question_id": "q1"}), "questions[0]", id="no-arms"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": None}), "questions[0]", id="null-arms"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": [1]}), "questions[0]", id="arms-not-an-object"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": {"skill": 5}}), "questions[0]",
                 id="arm-entry-not-an-object"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": {"skill": {"coverage": "x"}}}),
                 "questions[0]", id="coverage-not-a-number"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": {"skill": {"coverage": True}}}),
                 "questions[0]", id="bool-coverage"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": {"skill": {"coverage": float("nan")}}}),
                 "questions[0]", id="nan-coverage"),
    pytest.param("--checklist", _checklist({"question_id": "q1", "arms": {"skill": {"coverage": 10 ** 400}}}),
                 "questions[0]", id="coverage-past-float-range"),
])
def test_cross_judge_names_a_result_entry_of_the_wrong_shape_before_any_judge_call(
        tmp_path, capsys, monkeypatch, flag, content, where):
    from tests.test_judge_reliability import _cross_judge_args, _material_arms_run, _recording_judge

    seen: dict = {}
    _recording_judge(monkeypatch, seen)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(content), encoding="utf-8")
    args = _cross_judge_args(tmp_path, _material_arms_run(), "--no-stored", "--judges", "amazon-bedrock/m",
                             flag, str(wrong))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith(f"Error: {wrong}: {where} ")
    assert seen == {}  # no paid judge call was made


def test_cross_judge_takes_a_checklist_arm_with_a_null_coverage(tmp_path, monkeypatch):
    """`eval checklist` writes a null coverage for an arm with no scored point."""
    from dokimi import cli

    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    result = tmp_path / "result.json"
    result.write_text(json.dumps(_checklist({"question_id": "q1", "arms": {"skill": {"coverage": None}}})),
                      encoding="utf-8")
    out = tmp_path / "out.json"
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "cross-judge", "--answers", str(arms), "--no-stored",
                                     "--checklist", str(result), "--output", str(out)])
    cli.main()
    assert " · result |" in out.with_suffix(".md").read_text(encoding="utf-8")
