# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A score range the statistics refuse (#599, #638) must not lose the arms run (#750)."""

import json
import sys

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.eval.arm_runner import ArmRunner
from dokimi.report.arms_report import render_arms_report
from dokimi.treatments import BaselineTreatment

BIG = 1.7e308
QUESTIONS = [
    {"id": "q1", "question": "How do I parallelize a loop?"},
    {"id": "q2", "question": "How do I sum an array in parallel?"},
]


def _fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
    return "an answer", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


class _Judge:
    model = "fake-judge"
    provider = "fake-provider"


def _ev(qid, scores):
    return {"question_id": qid, "scores": {k: {"aggregate": v} for k, v in scores.items()}}


def _run(monkeypatch, arms, evaluations, questions=QUESTIONS):
    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", _fake_call)
    runner = ArmRunner([BaselineTreatment(name) for name in arms])
    records = runner.run("oneTBB", questions, concurrency=1)
    return runner, records, runner.build_output("oneTBB", records, evaluations, judge=_Judge())


def _saved(runner, output, tmp_path):
    path = tmp_path / "arms.json"
    runner.save(output, path)
    return json.loads(path.read_text(encoding="utf-8"))


def test_overflowing_paired_difference_keeps_the_run(monkeypatch, tmp_path, caplog):
    evaluations = [
        _ev("q1", {"baseline": -BIG, "a": BIG}),
        _ev("q2", {"baseline": 0.0, "a": 0.0}),
    ]
    runner, records, output = _run(monkeypatch, ["baseline", "a"], evaluations)

    assert output["answers"] == records
    assert output["evaluations"] == evaluations
    assert "significance_error, the run is kept without it" in caplog.text
    summary = output["summary"]
    assert "float range" in summary["significance_error"]
    assert "significance" not in summary["per_arm"]["a"]
    assert summary["per_arm"]["a"]["delta_vs_baseline"] is None
    assert "float range" in summary["per_arm"]["a"]["delta_error"]
    assert "interactions_error" not in summary

    saved = _saved(runner, output, tmp_path)
    assert saved["evaluations"] == evaluations
    assert saved["summary"]["significance_error"] == summary["significance_error"]


def test_overflowing_interaction_contrast_keeps_the_arm_significance(monkeypatch, tmp_path):
    evaluations = [
        _ev("q1", {"baseline": 0.0, "a": -BIG, "b": 0.0, "a+b": BIG}),
        _ev("q2", {"baseline": 0.0, "a": 0.0, "b": 0.0, "a+b": 0.0}),
    ]
    runner, records, output = _run(monkeypatch, ["baseline", "a", "b", "a+b"], evaluations)

    assert output["answers"] == records
    summary = output["summary"]
    assert "float range" in summary["interactions_error"]
    assert "interactions" not in summary
    assert "significance_error" not in summary
    assert summary["per_arm"]["a"]["significance"]["n"] == 2

    saved = _saved(runner, output, tmp_path)
    assert saved["summary"]["interactions_error"] == summary["interactions_error"]
    assert saved["summary"]["per_arm"]["a"]["significance"]["n"] == 2


def test_report_states_why_the_sections_are_missing(monkeypatch, tmp_path):
    evaluations = [
        _ev("q1", {"baseline": -BIG, "a": BIG}),
        _ev("q2", {"baseline": 0.0, "a": 0.0}),
    ]
    runner, _, output = _run(monkeypatch, ["baseline", "a"], evaluations)
    output["summary"]["interactions_error"] = "contrast too large"
    text = render_arms_report(_saved(runner, output, tmp_path))

    assert "Not computed: " + output["summary"]["significance_error"] in text
    assert "Not computed: contrast too large" in text


def _strict_json(path):
    def refuse(constant):
        raise AssertionError(f"{constant} is not JSON")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=refuse)


def test_saved_averages_of_extreme_scores_are_finite_json(monkeypatch, tmp_path):
    evaluations = [
        _ev("q1", {"baseline": -BIG, "a": BIG}),
        _ev("q2", {"baseline": -BIG, "a": BIG}),
    ]
    runner, _, output = _run(monkeypatch, ["baseline", "a"], evaluations)
    path = tmp_path / "arms.json"
    runner.save(output, path)

    per_arm = _strict_json(path)["summary"]["per_arm"]
    assert per_arm["baseline"]["avg_aggregate"] == -BIG
    assert per_arm["a"]["avg_aggregate"] == BIG


def test_saved_average_of_three_maximum_scores_is_the_maximum(monkeypatch, tmp_path):
    top = sys.float_info.max
    evaluations = [_ev(f"q{i}", {"baseline": top, "a": top}) for i in range(1, 4)]
    questions = [{"id": f"q{i}", "question": f"question {i}"} for i in range(1, 4)]
    runner, _, output = _run(monkeypatch, ["baseline", "a"], evaluations, questions)
    path = tmp_path / "arms.json"
    runner.save(output, path)

    per_arm = _strict_json(path)["summary"]["per_arm"]
    assert per_arm["baseline"]["avg_aggregate"] == sys.float_info.max
    assert per_arm["a"]["avg_aggregate"] == sys.float_info.max


def test_delta_whose_sum_leaves_the_range_is_averaged_exactly(monkeypatch, tmp_path):
    evaluations = [
        _ev("q1", {"baseline": 0.0, "a": 1.0e308}),
        _ev("q2", {"baseline": 0.0, "a": 1.0e308}),
    ]
    runner, _, output = _run(monkeypatch, ["baseline", "a"], evaluations)
    path = tmp_path / "arms.json"
    runner.save(output, path)

    arm = _strict_json(path)["summary"]["per_arm"]["a"]
    assert arm["delta_vs_baseline"] == 1.0e308
    assert "delta_error" not in arm


def test_cancelling_deltas_average_to_zero_like_the_scores(monkeypatch, tmp_path):
    evaluations = [
        _ev("q1", {"baseline": 0.0, "a": 1.0e308}),
        _ev("q2", {"baseline": 1.0e308, "a": 0.0}),
        _ev("q3", {"baseline": 0.0, "a": 0.0}),
    ]
    questions = [{"id": f"q{i}", "question": f"question {i}"} for i in range(1, 4)]
    runner, _, output = _run(monkeypatch, ["baseline", "a"], evaluations, questions)
    path = tmp_path / "arms.json"
    runner.save(output, path)

    arm = _strict_json(path)["summary"]["per_arm"]["a"]
    assert arm["delta_vs_baseline"] == 0.0
    assert "delta_error" not in arm


def test_refused_delta_of_a_thin_arm_keeps_another_arms_significance(monkeypatch, tmp_path):
    questions = [{"id": f"q{i}", "question": f"question {i}"} for i in range(1, 9)]
    evaluations = [_ev("q1", {"baseline": -BIG, "a": BIG, "b": -BIG})] + [
        _ev(f"q{i}", {"baseline": 0.0, "b": 1.0}) for i in range(2, 9)
    ]
    runner, _, output = _run(monkeypatch, ["baseline", "a", "b"], evaluations, questions)
    summary = output["summary"]

    assert "float range" in summary["per_arm"]["a"]["delta_error"]
    assert summary["per_arm"]["a"]["delta_vs_baseline"] is None
    assert "significance_error" not in summary
    assert summary["per_arm"]["b"]["significance"]["n"] == 8
    text = render_arms_report(_saved(runner, output, tmp_path))
    assert "Not computed:" not in text
    table = text.split("## Paired significance vs baseline")[1]
    assert "| `b` |" in table
    assert f"`a` not computed: {summary['per_arm']['a']['delta_error']}" in table


def test_console_summary_prints_an_arms_delta_refusal(monkeypatch, tmp_path, capsys):
    import dokimi.eval.arms_run as arms_run
    from dokimi.cli import build_parser
    from dokimi.commands import arms as arms_cmd

    summary = {"per_arm": {
        "baseline": {"avg_aggregate": 10.0, "n": 2, "errors": 0, "delta_vs_baseline": None},
        "a": {"avg_aggregate": 20.0, "n": 2, "errors": 0, "delta_vs_baseline": None, "delta_error": "too large"},
        "b": {"avg_aggregate": 30.0, "n": 2, "errors": 0, "delta_vs_baseline": 20.0},
    }}
    monkeypatch.setattr(arms_cmd, "_load_questions", lambda path: QUESTIONS)
    monkeypatch.setattr(arms_run, "prepare_arms", lambda *a, **k: None)
    monkeypatch.setattr(arms_run, "run_prepared", lambda *a, **k: {"summary": summary})
    monkeypatch.setattr(arms_cmd, "_save_arms_output", lambda *a, **k: None)
    args = build_parser().parse_args([
        "arms", "run", "--product", "p", "--questions", "q.json", "--arms", "baseline,a,b",
    ])
    args.func(args)

    lines = capsys.readouterr().out.splitlines()
    assert any(line.split()[:2] == ["a", "20.0"] and "(delta not computed: too large)" in line for line in lines)
    assert any("(delta +20.0)" in line for line in lines)


def test_no_refusal_key_without_overflow(monkeypatch):
    evaluations = [
        _ev("q1", {"baseline": 50.0, "a": 60.0}),
        _ev("q2", {"baseline": 52.0, "a": 55.0}),
    ]
    _, _, output = _run(monkeypatch, ["baseline", "a"], evaluations)

    assert not {"significance_error", "interactions_error"} & set(output["summary"])
