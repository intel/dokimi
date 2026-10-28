# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Wiring checks for the Stack P fixes of #228 step 2.

The parity table in ``test_answering_stack_parity.py`` pins what each fix changes
in numbers. These tests cover the parts that table cannot see: the value a flag
parses to, and where it lands.
"""

from types import SimpleNamespace

from tests.test_answering_stack_parity import _subparser


def test_benchmark_run_judge_temperature_reaches_the_pipeline(monkeypatch, tmp_path):
    from dokimi.commands import benchmark
    from dokimi import orchestrator

    args = _subparser("benchmark", "run").parse_args(
        ["--library", "onetbb", "--judge-temperature", "0.2"]
    )
    assert args.judge_temperature == 0.2

    seen = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, concurrency):
            return {"steps": {}}

    monkeypatch.setattr(orchestrator, "EvaluationPipeline", FakePipeline)
    entry = SimpleNamespace(
        name="oneTBB", key="onetbb", repo=None, description="d",
        doc_sources=["context7"], context7_id="uxlfoundation/oneTBB",
    )
    benchmark._run_single_library(
        entry,
        output_dir=str(tmp_path),
        model="m",
        provider="openai",
        judge_model="jm",
        judge_provider="openai",
        judge_temperature=args.judge_temperature,
    )
    assert seen["judge_temperature"] == 0.2


def test_benchmark_run_judge_temperature_defaults_to_provider_default():
    args = _subparser("benchmark", "run").parse_args(["--library", "onetbb"])
    assert args.judge_temperature is None


def test_judge_placeholder_flag_is_off_by_default_and_parses_when_given():
    run = _subparser("benchmark", "run")
    assert run.parse_args(["--library", "onetbb"]).judge_placeholder is False
    assert run.parse_args(["--library", "onetbb", "--judge-placeholder"]).judge_placeholder is True


def test_judge_placeholder_flag_judges_the_placeholder_answers(monkeypatch, tmp_path, provider_calls):
    from tests.answering_stack_parity import run_stack_p

    # q-empty and q-rerank carry the placeholder as their with-docs answer: with the flag they
    # are judged again, so P's judge calls go back to the 12 that Stack A makes.
    p = run_stack_p(monkeypatch, tmp_path, provider_calls, judge_placeholder=True)
    assert len(p.judge_inputs) == 12
    assert len([e for e in p.evaluations if e["delta"] is not None]) == 6


def test_eval_output_is_stamped_with_the_method_version(monkeypatch):
    from dokimi.defaults import ANSWERING_METHOD_VERSION
    from dokimi.eval.judge import Judge

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    out = Judge(model="gpt-x", provider="openai")._build_evaluation_output([])
    assert out["method_version"] == ANSWERING_METHOD_VERSION == 2
