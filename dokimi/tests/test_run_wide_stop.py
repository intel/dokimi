# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run-wide CLI failure ends the run visibly: the artifact says so, the command exits 1, and what
was answered is kept (including the completed arms of the question that stopped)."""

from __future__ import annotations

import json
import threading

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
    assert saved["terminated"] == {"reason": "usage limit exceeded", "questions_requested": 4,
                                   "questions_recorded": 2}
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


def _terminated_output() -> dict:
    return {"terminated": {"reason": "usage limit", "questions_requested": 4, "questions_recorded": 1}}


def test_arms_run_exits_1_and_names_the_stop_when_the_artifact_is_terminated(monkeypatch, tmp_path, capsys):
    from dokimi.commands import arms as arms_cmd
    from dokimi.eval import arms_run

    monkeypatch.setattr(arms_cmd, "_parse_arm_specs", lambda s: [])
    monkeypatch.setattr(arms_cmd, "_load_questions", lambda p: [])
    monkeypatch.setattr(arms_cmd, "run_config_from_args", lambda a: SimpleNamespace(product="lib"))
    monkeypatch.setattr(arms_cmd, "_with_named_cell", lambda cfg, cells, cell: cfg)
    monkeypatch.setattr(arms_run, "prepare_arms", lambda cfg, specs, questions: object())
    monkeypatch.setattr(arms_run, "run_prepared", lambda prepared, cfg, questions, on_partial=None: _terminated_output())
    monkeypatch.setattr(arms_cmd, "_save_arms_output", lambda output, out_json, out_md, partial=None: None)
    args = SimpleNamespace(arms="baseline", questions="q.json", matrix_cells=None, matrix_cell=None,
                           product="lib", out_json=str(tmp_path / "a.json"), out_md=str(tmp_path / "a.md"),
                           baseline_arm="baseline")

    with pytest.raises(SystemExit) as exc:
        arms_cmd.cmd_arms_run(args)
    assert exc.value.code == 1
    assert "stopped after 1 of 4 questions: usage limit" in capsys.readouterr().err


def test_a_matrix_stops_at_the_terminated_cell_after_saving_it(monkeypatch):
    from dokimi.eval import arms_run

    monkeypatch.setattr(arms_run, "check_matrix", lambda config, cells, specs: None)
    monkeypatch.setattr(arms_run, "cell_config", lambda config, cell: config)
    monkeypatch.setattr(arms_run, "prepare_arms", lambda cfg, specs, questions: object())
    ran, saved = [], []
    monkeypatch.setattr(arms_run, "run_prepared", lambda prepared, cfg, questions, on_partial=None: (ran.append(cfg), _terminated_output())[1])
    cells = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]

    with pytest.raises(arms_run.MatrixCellError) as exc:
        arms_run.run_matrix(SimpleNamespace(), cells, [], [], on_cell=lambda cell, out: saved.append(cell.name))
    assert exc.value.cell == "first" and exc.value.usage is False
    assert saved == ["first"] and len(ran) == 1  # the stopped cell is kept; the second cell is never run


def test_a_baseline_stop_keeps_the_context_answer_already_paid_for(monkeypatch):
    # The context answer is generated first; a usage limit on the baseline call must not discard it.
    answerer = Answerer(mcp_client=object(), model="gpt-4o-mini", provider="openai")
    monkeypatch.setattr(answerer, "_retrieve_docs", lambda *a, **k: (["doc"], {}))
    paid = {"answer": "with docs", "token_usage": {"total_tokens": 9}}
    monkeypatch.setattr(answerer, "_generate_with_docs", lambda *a, **k: paid)

    def baseline(*a, **k):
        raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)

    monkeypatch.setattr(answerer, "_generate_without_docs", baseline)
    answers = answerer.generate_answers("lib", "/lib/id", QUESTIONS[:1], concurrency=1)
    assert len(answers) == 1
    assert answers[0]["with_docs"] == paid  # the paid-for condition and its telemetry survive
    assert answers[0]["without_docs"] is None and answers[0]["error"].startswith("usage limit")
    assert answerer.stop_error is not None


def test_a_worker_in_flight_does_not_launch_the_next_arm_after_another_worker_stopped(monkeypatch):
    # q2's first arm is in flight when q1 hits a usage limit. Its second arm must not be paid for.
    in_flight = threading.Event()
    stops = []  # the run's stop event, seen by the check before each arm
    calls = []
    real_halt = arm_runner_module.halt_if_stopped
    runner = ArmRunner([BaselineTreatment(), BaselineTreatment(name="other")], model="gpt-4o-mini",
                       provider="openai")

    def call(**kwargs):
        calls.append(kwargs["prompt"])
        if "parallelize" in kwargs["prompt"]:
            in_flight.wait(timeout=10)
            raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)
        in_flight.set()
        assert stops[0].wait(timeout=10)  # q2's first arm returns only once the run is stopped
        return "answer", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    def halt_if_stopped(stop):
        stops.append(stop)
        return real_halt(stop)

    monkeypatch.setattr(arm_runner_module, "llm_call_with_usage", call)
    monkeypatch.setattr(arm_runner_module, "halt_if_stopped", halt_if_stopped)
    records = runner.run("lib", QUESTIONS, concurrency=2)
    assert len(calls) == 2  # q1's failing call and q2's first arm; no second arm of q2
    by_id = {r["question_id"]: r["arms"] for r in records}
    assert by_id["q2"]["baseline"]["answer"] == "answer"  # the completed cell is kept
    assert by_id["q2"]["other"]["error"].startswith("not_asked")
    assert runner.stop_error is not None


def test_run_prepared_stamps_the_terminated_block_of_a_run_wide_stop(monkeypatch):
    # The arms artifact says the run stopped, with the counts: the stamp is what a reader sees, not only the exit code.
    from dokimi.eval import arms_run

    _usage_stop(monkeypatch, arm_runner_module, stop_on_call=2)
    questions = [{"id": f"q{i}", "question": f"question {i}"} for i in range(4)]
    prepared = arms_run.PreparedArms(runner=ArmRunner([BaselineTreatment()]), judge=None, library_id="lib")
    output = arms_run.run_prepared(prepared, SimpleNamespace(product="lib", concurrency=1, baseline_arm="baseline"),
                                   questions)
    assert output["terminated"] == {"reason": "usage limit exceeded", "questions_requested": 4,
                                    "questions_recorded": 2}


def test_a_stopped_answers_run_is_not_judged_or_reported_as_whole(monkeypatch, tmp_path):
    from dokimi.orchestrator import pipeline as pipeline_mod

    def generate(self, *args, **kwargs):
        self.stop_error = llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)
        return [{"question_id": "q0", "with_docs": None, "without_docs": None, "error": "usage limit exceeded"}]

    monkeypatch.setattr(pipeline_mod, "create_doc_source_client", lambda source: None)
    monkeypatch.setattr(Answerer, "generate_answers", generate)
    monkeypatch.setattr(Answerer, "save_answers", lambda self, answers, path, question_set_hash=None: None)
    fake = SimpleNamespace(doc_source="none", model="m", provider="claude-cli", top_k=5, rerank_threshold=0.5,
                           debug_retrieval=False, product="lib", max_tokens_per_question=100,
                           answers_path=tmp_path / "answers.json", _library_id=lambda client: "lib/id")
    with pytest.raises(RuntimeError, match="stopped after 1 of 2 questions"):
        pipeline_mod.EvaluationPipeline._generate_answers(fake, [{"id": "q0"}, {"id": "q1"}], concurrency=1)


def test_a_kept_context_answer_keeps_its_question_ground_truth(monkeypatch):
    answerer = Answerer(mcp_client=object(), model="gpt-4o-mini", provider="openai")
    monkeypatch.setattr(answerer, "_retrieve_docs", lambda *a, **k: (["doc"], {}))
    monkeypatch.setattr(answerer, "_generate_with_docs", lambda *a, **k: {"answer": "with docs"})

    def baseline(*a, **k):
        raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)

    monkeypatch.setattr(answerer, "_generate_without_docs", baseline)
    question = {"id": "q1", "question": "How do I sum?", "ground_truth_chunk": "chunk text",
                "question_source": "chunk"}
    [row] = answerer.generate_answers("lib", "/lib/id", [question], concurrency=1)
    assert row["ground_truth_chunk"] == "chunk text" and row["question_source"] == "chunk"


def test_run_arms_on_a_usage_limit_writes_the_terminated_block(monkeypatch):
    from dokimi.eval import arms_run

    calls = _usage_stop(monkeypatch, arm_runner_module, stop_on_call=2)
    config = arms_run.ArmsRunConfig(product="lib", model="gpt-4o-mini", provider="openai", concurrency=1)

    output = arms_run.run_arms(config, ["baseline"], QUESTIONS)

    assert len(calls) == 2  # the second question's call is the one that hits the limit
    assert output["terminated"] == {"reason": "usage limit exceeded", "questions_requested": 2,
                                    "questions_recorded": 2}
