# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Knowledge runs (docs/knowledge-runs.md): one question set, one condition, graded offline.

The answering model is faked at the ``dokimi.llm`` seam (``fake_llm``) or, for the agent CLI, at
the subprocess seam (``FakeRunner``), so no provider or CLI is reached.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from dokimi import llm_cli
from dokimi.cli import build_parser
from dokimi.eval.arms_refusals import refuse_task_harness
from dokimi.eval.arms_run import ArmsRunConfig, prepare_arms, run_arms
from dokimi.process import FakeRunner, reply
from dokimi.utils import InputError
from tests.cli_refusal import refusal

QUESTIONS = [
    {"id": "q1", "question": "Which function runs a parallel for-loop in oneTBB?",
     "grading": {"checks": [{"type": "match", "values": ["parallel_for"], "location": "any"}]}},
    {"id": "q2", "question": "Which header declares it?",
     "grading": {"checks": [{"type": "match", "values": ["tbb/parallel_for.h"], "location": "any"}]}},
]

JUDGE_FIELDS = {"aggregate": 70, "correctness": 70, "completeness": 70, "specificity": 70,
                "code_quality": 70, "actionability": 70, "reasoning": "ok"}


def _respond(request):
    """Judge calls get the judge JSON; every answer names parallel_for and no header."""
    prompt = request.messages[-1]["content"]
    if "score" in prompt.lower():
        return json.dumps(JUDGE_FIELDS)
    return "Use parallel_for from the oneTBB headers."


def _write_questions(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": QUESTIONS}), encoding="utf-8")
    return path


def _cli_reply_text(text):
    return json.dumps({"type": "result", "is_error": False, "result": text, "stop_reason": "end_turn",
                       "total_cost_usd": 0.001,
                       "usage": {"input_tokens": 2, "output_tokens": 3,
                                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                       "modelUsage": {"sonnet": {"costUSD": 0.001, "costBasis": "list"}}})


@pytest.fixture
def cli_runner(monkeypatch, tmp_path):
    """Every ``claude`` call answers ``Use parallel_for ...`` and is recorded in ``runner.calls``."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    fake = FakeRunner(lambda call: reply(_cli_reply_text("Use parallel_for from the oneTBB headers.")))
    monkeypatch.setattr(llm_cli.process, "run", fake)
    monkeypatch.setattr(llm_cli, "cli_version", lambda provider: "2.1.303 (Claude Code)")
    return fake


# --- one arm: the run is accepted and has no delta ----------------------------------------------


def test_a_one_arm_judged_run_is_a_knowledge_run_with_no_delta(fake_llm):
    fake_llm.respond = _respond
    config = ArmsRunConfig(product="oneTBB", model="claude-haiku-5-5", provider="amazon-bedrock",
                           judge=True, judge_model="claude-sonnet-5-5", judge_provider="amazon-bedrock")
    out = run_arms(config, ["baseline"], QUESTIONS)

    assert out["arms"] == ["baseline"]
    per_arm = out["summary"]["per_arm"]
    assert set(per_arm) == {"baseline"}
    assert per_arm["baseline"]["avg_aggregate"] == pytest.approx(70)
    assert per_arm["baseline"].get("delta_vs_baseline") is None


def test_a_one_arm_run_without_a_judge_only_answers(fake_llm):
    fake_llm.respond = _respond
    config = ArmsRunConfig(product="oneTBB", model="claude-haiku-5-5", provider="amazon-bedrock")
    out = run_arms(config, ["baseline"], QUESTIONS)

    assert out["arms"] == ["baseline"]
    assert "summary" not in out  # nothing is scored, so there is no per-arm average to report
    assert not any("score" in req.messages[-1]["content"].lower() for req in fake_llm.requests)


# --- grading an arms.v1 one-arm run with eval score reference ------------------------------------


def test_eval_score_reference_grades_the_single_arm_of_an_arms_run(fake_llm, tmp_path):
    fake_llm.respond = _respond
    config = ArmsRunConfig(product="oneTBB", model="claude-haiku-5-5", provider="amazon-bedrock")
    arms_out = run_arms(config, ["baseline"], QUESTIONS)
    arms_path = tmp_path / "arms.json"
    arms_path.write_text(json.dumps(arms_out), encoding="utf-8")
    questions_path = _write_questions(tmp_path)
    out = tmp_path / "eval.json"

    args = build_parser().parse_args(["eval", "score", "reference", "--answers", str(arms_path),
                                      "--questions", str(questions_path), "-o", str(out), "--no-judge"])
    args.func(args)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["arms"] == ["baseline"]  # an arms input stays arms.v1; its tally sits under reference_scoring
    summary = data["reference_scoring"]["summary"]
    assert set(summary) == {"baseline"}
    assert summary["baseline"]["n"] == 2
    assert summary["baseline"]["pass_rate"] == pytest.approx(0.5)  # q1 names parallel_for, q2 names no header


# --- a plain answers file: answers generate --doc-source none has no context arm -----------------


def test_a_knowledge_answers_run_prints_baseline_and_no_context_arm(capsys):
    from dokimi.eval.answerer import Answerer

    answerer = Answerer(mcp_client=None, model="claude-haiku-5-5", provider="amazon-bedrock")
    with mock.patch("dokimi.eval.answerer.llm_call_with_usage", return_value=("an answer", {})):
        answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", QUESTIONS, concurrency=1)

    printed = capsys.readouterr().out
    assert "Context arm" not in printed
    assert "Baseline:     2/2" in printed
    assert all(a["with_docs"] is None and a["without_docs"]["answer"] == "an answer" for a in answers)


# --- agent CLI as the answering harness -----------------------------------------------------------


def test_claude_cli_answers_a_one_arm_knowledge_run(cli_runner):
    config = ArmsRunConfig(product="oneTBB", model="sonnet", provider="claude-cli")
    out = run_arms(config, ["baseline"], QUESTIONS[:1])

    assert out["arms"] == ["baseline"]
    assert cli_runner.calls, "the answer must go through the claude CLI"
    argv = cli_runner.calls[0].argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    answers = json.dumps(out["answers"])
    assert "Use parallel_for from the oneTBB headers." in answers
    assert "generation_failed" not in answers


def test_claude_cli_refuses_a_temperature_before_any_call(cli_runner):
    config = ArmsRunConfig(product="oneTBB", model="sonnet", provider="claude-cli", temperature=0.0)
    with pytest.raises(ValueError, match="temperature"):
        prepare_arms(config, ["baseline"], QUESTIONS[:1])
    assert cli_runner.calls == []


# --- task-harness labels are refused: the answers come from --provider ----------------------------


@pytest.mark.parametrize("harness", ["codex", "codex-review", "claude-code", "copilot", "copilot-review",
                                     "terminal-bench:codex", "command:echo hi", "docker-codex", "benchflow-codex"])
def test_a_task_harness_label_is_refused_before_any_answer(harness, fake_llm):
    config = ArmsRunConfig(product="oneTBB", model="claude-haiku-5-5", provider="amazon-bedrock", harness=harness)
    with pytest.raises(InputError, match="names a task harness"):
        prepare_arms(config, ["baseline"], QUESTIONS[:1])
    assert fake_llm.requests == []


@pytest.mark.parametrize("harness", ["arms-runner", "single-shot", "agent", "openclaw-agent", "my-custom"])
def test_the_arms_harness_labels_are_accepted(harness):
    assert refuse_task_harness(ArmsRunConfig(product="oneTBB", harness=harness)) is None


def test_the_default_harness_is_accepted():
    assert refuse_task_harness(ArmsRunConfig(product="oneTBB")) is None


def test_the_harness_refusal_reaches_the_cli_with_exit_2(tmp_path, fake_llm):
    args = build_parser().parse_args(["arms", "run", "--product", "oneTBB", "--questions",
                                      str(_write_questions(tmp_path)), "--arm", "baseline",
                                      "--model", "amazon-bedrock/claude-haiku-5-5", "--harness", "codex"])
    with refusal() as outcome:
        args.func(args)
    assert outcome.code == 2
    assert "task harness" in outcome.err
    assert "--provider claude-cli --model sonnet" in outcome.err
    assert fake_llm.requests == []


# --- codex-cli has no tool-off switch, so it cannot answer a knowledge run ----------------------


def test_codex_cli_refuses_a_one_arm_knowledge_run_before_any_call(cli_runner):
    config = ArmsRunConfig(product="oneTBB", model="gpt-5.5", provider="codex-cli",
                              judge_model="claude-sonnet-5-5", judge_provider="amazon-bedrock")
    with pytest.raises(InputError, match="codex-cli.*knowledge run"):
        prepare_arms(config, ["baseline"], QUESTIONS[:1])
    assert cli_runner.calls == []


def test_codex_cli_answers_an_arms_run_with_a_docs_arm(cli_runner):
    """Only the knowledge run is refused: with retrieval the codex answers are a comparison arm."""
    from dokimi.eval.arms_refusals import refuse_cli_arms

    config = ArmsRunConfig(product="oneTBB", model="gpt-5.5", provider="codex-cli",
                              judge_model="claude-sonnet-5-5", judge_provider="amazon-bedrock")
    treatments = [type("T", (), {"name": n})() for n in ("baseline", "docs")]
    refuse_cli_arms(config, treatments)
    assert cli_runner.calls == []


def test_codex_cli_refuses_an_answers_knowledge_run_with_exit_2(tmp_path, cli_runner):
    args = build_parser().parse_args(["answers", "generate", "--product", "oneTBB", "--questions",
                                      str(_write_questions(tmp_path)), "--doc-source", "none",
                                      "--model", "codex-cli/gpt-5.5", "--output", str(tmp_path / "answers.json")])
    with refusal() as outcome:
        args.func(args)
    assert outcome.code == 2
    assert "codex-cli" in outcome.err and "knowledge run" in outcome.err
    assert cli_runner.calls == []
