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
