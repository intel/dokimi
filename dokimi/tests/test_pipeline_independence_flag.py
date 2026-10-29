# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``dokimi run`` stores the same ``evaluator_independence_warning`` that ``dokimi eval`` does."""

from __future__ import annotations

import types

import pytest

from dokimi.orchestrator.pipeline import EvaluationPipeline


@pytest.mark.parametrize("provider, model, judge_provider, judge_model, expected", [
    ("anthropic", "claude-sonnet-5-5", "amazon-bedrock", "claude-sonnet-5-5", True),  # one model, two routes
    ("anthropic", "claude-sonnet-5-5", "anthropic", "claude-sonnet-5-5", True),
    ("amazon-bedrock", "us.anthropic.claude-sonnet-5-5-v1:0", "amazon-bedrock", "claude-sonnet-5-5", True),
    ("anthropic", "claude-sonnet-5-5", "anthropic", "claude-haiku-5-5", False),
])
def test_the_stored_flag_follows_the_same_model_rule(tmp_path, monkeypatch, provider, model, judge_provider,
                                                     judge_model, expected):
    seen = {}

    class FakeJudge:
        def __init__(self, **kwargs):
            seen.update(kwargs["run_metadata"])

        def evaluate_answers(self, *args, **kwargs):
            return []

        def save_evaluations(self, *args, **kwargs):
            pass

    monkeypatch.setattr("dokimi.eval.Judge", FakeJudge)
    monkeypatch.setattr("dokimi.records.read", lambda *a, **k: types.SimpleNamespace(records=[{"id": "q1"}]))
    pipeline = EvaluationPipeline(product="oneTBB", output_dir=tmp_path, description="d", model=model,
                                  provider=provider,
                                  judge_model=judge_model, judge_provider=judge_provider)

    pipeline._evaluate_answers([{"id": "q1"}])

    assert seen["evaluator_independence_warning"] is expected
