# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""arms run / subjects run warn (never refuse) when the judge is the answer model."""

from dokimi.eval.arms_run import ArmsRunConfig, warn_self_judging


def test_same_model_warns(capsys):
    cfg = ArmsRunConfig(product="p", model="gpt-6.1-sol", provider="openai",
                        judge=True, judge_model="gpt-6.1-sol", judge_provider="openai")
    assert warn_self_judging(cfg) is True
    assert "Evaluator independence warning" in capsys.readouterr().err


def test_other_family_is_silent(capsys):
    cfg = ArmsRunConfig(product="p", model="gpt-6.1-sol", provider="openai",
                        judge=True, judge_model="claude-sonnet-5-5", judge_provider="anthropic")
    assert warn_self_judging(cfg) is False
    assert capsys.readouterr().err == ""


def test_check_judge_calls_the_warning(monkeypatch):
    """`arms run --judge` and `subjects run --judge` both go through _check_judge."""
    from dokimi.eval import arms_run

    class Stop(Exception):
        pass

    monkeypatch.setattr(arms_run, "check_bedrock_model", lambda *a: None)
    monkeypatch.setattr(arms_run, "probe_sampling", lambda *a: None)

    def stop(cfg):
        raise Stop

    monkeypatch.setattr(arms_run, "warn_self_judging", stop)
    cfg = ArmsRunConfig(product="p", judge=True, judge_model="m", judge_provider="openai")
    import pytest

    with pytest.raises(Stop):
        arms_run._check_judge(cfg, [{"question": "q"}])
