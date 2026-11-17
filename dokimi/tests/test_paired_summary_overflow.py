# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A paired difference beyond the float range leaves ``paired_summary`` without a delta, not without a result.

https://github.com/intel/dokimi/issues/830
"""

from __future__ import annotations

import json
import statistics

import pytest

from dokimi.eval import stats
from dokimi.gate.trust_gate import evaluate_trust
from dokimi.orchestrator.pipeline import EvaluationPipeline
from dokimi.report.eval_report import generate_report
from dokimi.report.generator import ReportGenerator

BIG = 1.7e308


def _row(qid, with_score, without_score):
    return {"question_id": qid, "question_text": f"Question {qid}?",
            "with_docs": {"aggregate": with_score}, "without_docs": {"aggregate": without_score}}


# The issue's input: one pair far apart, one pair equal.
ISSUE_INPUT = [_row("q1", BIG, -BIG), _row("q2", 0.0, 0.0)]


def test_paired_summary_of_the_issue_input_has_no_delta_and_keeps_the_rest():
    s = stats.paired_summary(ISSUE_INPUT)
    assert (s.delta_mean, s.delta_min, s.delta_max) == (None, None, None)
    assert s.delta_overflow == 1


def test_paired_summary_keeps_the_arm_means_ranges_and_counts_of_an_overflowing_pair():
    s = stats.paired_summary(ISSUE_INPUT + [_row("q3", 10.0, 4.0), {"question_id": "q4", "with_docs": {"aggregate": 1.0}}])
    assert (s.total, s.paired, s.unpaired) == (4, 3, 1)
    assert (s.with_mean, s.without_mean) == (statistics.mean([BIG, 0.0, 10.0]), statistics.mean([-BIG, 0.0, 4.0]))
    assert (s.with_min, s.with_max, s.without_min, s.without_max) == (0.0, BIG, -BIG, 4.0)


def test_paired_summary_counts_an_overflowing_pair_by_comparing_its_scores():
    s = stats.paired_summary([_row("q1", BIG, -BIG), _row("q2", -BIG, BIG), _row("q3", 5.0, 5.0), _row("q4", 6.0, 5.0)])
    assert (s.improvements, s.degradations, s.neutral) == (2, 1, 1)


def test_paired_summary_of_pairs_inside_the_range_still_has_a_delta():
    s = stats.paired_summary([_row("q1", BIG, BIG - 1e300), _row("q2", 80.0, 60.0)])
    assert s.delta_max == pytest.approx(1e300, rel=1e-9)
    assert s.delta_min == 20.0


def _run_pipeline(tmp_path, monkeypatch, evaluations):
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", lambda self, *a, **k: [])
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", lambda self, *a, **k: evaluations)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", lambda self, *a, **k: "report")
    output_dir = tmp_path / "run"
    (output_dir / "personas").mkdir(parents=True)
    (output_dir / "questions").mkdir()
    (output_dir / "personas" / "oneTBB.json").write_text(json.dumps({"personas": []}), encoding="utf-8")
    questions = [{"id": e["question_id"], "question": e["question_text"], "source_type": "manual"} for e in evaluations]
    (output_dir / "questions" / "oneTBB.json").write_text(json.dumps({"questions": questions}), encoding="utf-8")
    pipeline = EvaluationPipeline(product="oneTBB", output_dir=output_dir, description="Threading Building Blocks")
    return pipeline.run(concurrency=1)


def test_pipeline_reaches_the_report_step_with_the_arm_averages_and_no_delta(tmp_path, monkeypatch, capsys):
    result = _run_pipeline(tmp_path, monkeypatch, ISSUE_INPUT)
    assert result["status"] == "success"
    step = result["steps"]["evaluation"]
    assert step["summary"]["delta_avg"] is None
    assert (step["paired"], step["unpaired"]) == (2, 0)
    assert "delta=none (a paired difference leaves the float range)" in capsys.readouterr().out
    assert "report" in result["steps"]


def test_report_generator_prints_an_em_dash_and_a_note_for_the_missing_delta():
    data = {"evaluations": ISSUE_INPUT}
    md = ReportGenerator().generate_report(data, {"questions": []}, "markdown")
    assert "| **—** |" in md
    assert "_No delta: 1 complete pairs differ by more than the float range._" in md
    assert "no delta and no conclusion" in md
    out = json.loads(ReportGenerator().generate_report(data, {"questions": []}, "json"))["stats"]
    assert out["delta"] == {"avg": None, "min": None, "max": None}
    assert out["with_docs"]["avg"] == pytest.approx(BIG / 2, rel=1e-12)
    assert (out["doc_improvements"], out["doc_degradations"]) == (1, 0)


def _reject_constant(name):
    raise AssertionError(f"non-standard JSON constant {name}")


def test_report_json_trust_reports_min_delta_not_measured_and_is_strict_json():
    rows = [_row(f"q{i}", BIG, -BIG) for i in range(3)]
    text = ReportGenerator().generate_report({"evaluations": rows}, {"questions": []}, "json")
    out = json.loads(text, parse_constant=_reject_constant)
    check = next(c for c in out["trust"]["checks"] if c["name"] == "min_delta")
    assert (check["value"], check["measured"], check["passed"]) == (None, False, True)
    assert "Not measured" in check["message"]
    assert "min_delta" in evaluate_trust(rows).not_measured
    assert out["trust"]["not_measured"] == evaluate_trust(rows).not_measured


def test_report_generator_group_tables_give_a_group_with_an_overflowing_pair_no_delta():
    questions = {"questions": [{"id": "q1", "question": "Q1?", "source_type": "manual", "topic": "t"},
                               {"id": "q2", "question": "Q2?", "source_type": "manual", "topic": "t"}]}
    out = json.loads(ReportGenerator().generate_report({"evaluations": ISSUE_INPUT}, questions, "json"))
    assert out["clusters"][0]["delta_avg"] is None
    assert out["clusters"][0]["count"] == 2
    assert out["sources_summary"][0]["delta_avg"] is None


def test_eval_report_agreement_line_says_none_when_the_dynamic_set_has_no_delta(tmp_path):
    rows = [_row("onetbb-Q001", 80.0, 60.0), _row("onetbb-Q002", 70.0, 50.0), _row("gen-1", BIG, -BIG), _row("gen-2", 0.0, 0.0)]
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": rows}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    text = md.read_text(encoding="utf-8")
    assert "- **Dynamic vs Static agreement:** none — a set has no delta (a paired difference leaves the float range)." in text


def test_cmd_evaluate_prints_a_dash_for_the_delta_of_a_summary_without_one(monkeypatch, capsys):
    import argparse

    from dokimi import orchestrator
    from dokimi.commands import orchestrate

    result = {"steps": {"personas": {"skipped": True}, "questions_merged": {"path": "q"}, "answers": {"path": "a"},
                        "report": {"path": "r"},
                        "evaluation": {"path": "e", "summary": {"with_avg": 1.0, "without_avg": 2.0, "delta_avg": None}}}}

    class FakePipeline:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return result

    monkeypatch.setattr(orchestrator, "EvaluationPipeline", FakePipeline)
    args = argparse.Namespace(repo="owner/repo", description=None, product="demo", output_dir="out", custom_questions=None,
                              provider="openai", model="m", judge_provider="openai", judge_model="j", judge_temperature=None,
                              personas_count=1, questions_per_topic=1, top_k=None, rerank_threshold=0.3, debug_retrieval=False)
    orchestrate.cmd_evaluate(args)
    out = capsys.readouterr().out
    assert "  Delta:            — (a paired difference leaves the float range)" in out
    assert "Context avg:      1.0" in out


def test_eval_report_prints_an_em_dash_for_the_missing_delta(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": ISSUE_INPUT}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    text = md.read_text(encoding="utf-8")
    assert "a paired difference leaves the float range, so there is no delta" in text
    assert "_Not computed: the difference of 1.7e+308 and -1.7e+308 leaves the float range._" in text
