"""A run-wide CLI failure ends the run visibly: the artifact says so, the command exits 1, and what
was answered is kept (including the completed arms of the question that stopped)."""

from __future__ import annotations

import json

from types import SimpleNamespace

import pytest

import dokimi.questions.artifact
from dokimi import llm_cli
from dokimi.commands import answers
from dokimi.eval import answerer as answerer_module
from dokimi.eval import arm_runner as arm_runner_module
from dokimi.eval.answerer import Answerer
from dokimi.eval.arm_runner import ArmRunner
from dokimi.treatments import BaselineTreatment
from tests.test_answers_command import _args, _stub_generate

QUESTIONS = [{"id": "q1", "question": "How do I parallelize a loop?"},
             {"id": "q2", "question": "How do I sum an array in parallel?"}]


def _usage_stop(monkeypatch, module, stop_on_call: int) -> list:
    """Live calls answer until call ``stop_on_call``, which hits a usage limit (run-wide)."""
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        if len(calls) == stop_on_call:
            raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)
        return f"answer {len(calls)}", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}

    monkeypatch.setattr(module, "llm_call_with_usage", call)
    return calls


def test_answers_generate_exits_1_after_a_run_wide_failure_and_keeps_what_was_answered(
        tmp_path, monkeypatch, capsys):
    _stub_generate(monkeypatch, tmp_path, Answerer)
    monkeypatch.setattr(dokimi.questions.artifact, "load_questions", lambda path: SimpleNamespace(
        questions=[{"id": f"q{i}", "question": f"question {i}"} for i in range(4)]))
    calls = _usage_stop(monkeypatch, answerer_module, stop_on_call=2)
    out = tmp_path / "answers.json"

    with pytest.raises(SystemExit) as exc:
        answers.cmd_answers_generate(_args(output=str(out), doc_source="none", concurrency=1,
                                         provider="openai", model="gpt-4o-mini"))

    assert exc.value.code == 1
    assert len(calls) == 2  # the questions not yet started are not paid for
    saved = json.loads(out.read_text(encoding="utf-8"))
    rows = saved["answers"]
    assert len(rows) == 2 and rows[0].get("error") is None and rows[1]["error"]
    assert "stopped after 2 of 4 questions" in capsys.readouterr().err


def test_arms_run_artifact_records_the_stop_and_keeps_the_completed_arms_of_that_question(
        monkeypatch):
    calls = _usage_stop(monkeypatch, arm_runner_module, stop_on_call=2)
    runner = ArmRunner([BaselineTreatment(), BaselineTreatment(name="other")], model="gpt-4o-mini", provider="openai")

    records = runner.run("lib", QUESTIONS[:1], concurrency=1)
    assert len(calls) == 2
    assert [r["question_id"] for r in records] == ["q1"]
    arms = records[0]["arms"]
    assert arms["baseline"].get("error") is None and arms["baseline"]["answer"]  # the paid-for arm survives
    assert arms["other"]["error"].startswith("generation_failed")
    assert runner.stop_error is not None and runner.questions_requested == 1


def test_an_arm_the_stop_never_reached_is_a_not_asked_cell_not_a_gap(monkeypatch):
    # The usage limit hits the first arm, so the second (the baseline here) never runs: its cell says so.
    calls = _usage_stop(monkeypatch, arm_runner_module, stop_on_call=1)
    runner = ArmRunner([BaselineTreatment(name="other"), BaselineTreatment()], model="gpt-4o-mini",
                       provider="openai")
    records = runner.run("lib", QUESTIONS[:1], concurrency=1)
    assert len(calls) == 1
    assert set(records[0]["arms"]) == {"other", "baseline"}
    assert records[0]["arms"]["baseline"]["error"].startswith("not_asked")
