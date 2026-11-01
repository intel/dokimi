# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Wiring checks for the Stack P fixes of #228 step 2.

The parity table in ``test_answering_stack_parity.py`` pins what each fix changes
in numbers. These tests cover the parts that table cannot see: the value a flag
parses to, and where it lands.
"""

import pytest

from tests.test_answering_stack_parity import _subparser


class _FakePipeline:
    """Stands in for EvaluationPipeline: keeps the kwargs ``benchmark run`` builds it with."""

    built: list = []

    def __init__(self, **kwargs):
        _FakePipeline.built.append(kwargs)

    def run(self, concurrency):
        return {"steps": {}}


@pytest.mark.parametrize("extra, runs", [([], 1), (["--multi-run", "2"], 2)], ids=["single-run", "multi-run"])
def test_benchmark_run_hands_the_judge_flags_to_the_pipeline(monkeypatch, tmp_path, extra, runs):
    """The real handler runs; only the pipeline is faked. Both flags reach it in every mode."""
    from dokimi.commands import benchmark
    from dokimi import orchestrator

    _FakePipeline.built = []
    monkeypatch.setattr(orchestrator, "EvaluationPipeline", _FakePipeline)
    args = _subparser("benchmark", "run").parse_args(
        ["--library", "onetbb", "--output-dir", str(tmp_path), "--judge-temperature", "0.3",
         "--judge-placeholder", *extra]
    )

    benchmark.cmd_benchmark_run(args)

    assert len(_FakePipeline.built) == runs
    for kwargs in _FakePipeline.built:
        assert kwargs["judge_temperature"] == 0.3
        assert kwargs["judge_placeholder"] is True


def test_benchmark_run_leaves_the_judge_flags_off_when_not_given(monkeypatch, tmp_path):
    from dokimi.commands import benchmark
    from dokimi import orchestrator

    _FakePipeline.built = []
    monkeypatch.setattr(orchestrator, "EvaluationPipeline", _FakePipeline)
    args = _subparser("benchmark", "run").parse_args(["--library", "onetbb", "--output-dir", str(tmp_path)])

    benchmark.cmd_benchmark_run(args)

    (kwargs,) = _FakePipeline.built
    assert kwargs["judge_temperature"] is None
    assert kwargs["judge_placeholder"] is False


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


def test_eval_output_says_whether_the_placeholder_was_judged(monkeypatch):
    """The two modes give different numbers on the same answers under one method_version."""
    from tests.artifact_fixtures import artifact_fixture
    from dokimi.eval.judge import Judge

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    default = Judge(model="gpt-x", provider="openai")._build_evaluation_output([])
    opted_in = Judge(model="gpt-x", provider="openai", judge_placeholder=True)._build_evaluation_output([])

    assert default["judge_placeholder"] is False
    assert opted_in["judge_placeholder"] is True
    artifact_fixture("eval", opted_in)  # eval.v1 declares the key
