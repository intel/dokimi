# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A seed the route does not take is warned about once, dropped, and recorded as unsupported.

The run is not refused: the seed was never sent, so the artifact must not read as seeded.
A temperature the route refuses still refuses the run.
"""

import pytest

from dokimi.llm_sampling import NAME_REFUSAL, warn_seed_dropped
from dokimi.treatments import BaselineTreatment


@pytest.fixture(autouse=True)
def _fresh_warning():
    warn_seed_dropped.cache_clear()
    yield
    warn_seed_dropped.cache_clear()


def test_arms_run_drops_a_seed_the_route_refuses_and_warns_once(monkeypatch, capsys):
    from dokimi.eval import arms_run
    from dokimi.eval.arms_run import ArmsRunConfig, check_sampling_support

    probed = []
    monkeypatch.setattr("dokimi.llm.sampling_refusals", lambda *a, **k: {"seed": NAME_REFUSAL})
    monkeypatch.setattr(arms_run, "probe_sampling", lambda model, provider, temperature, seed: probed.append(seed))
    config = ArmsRunConfig(product="demo", model="m", provider="amazon-bedrock", seed=7)

    first = check_sampling_support(config)
    second = check_sampling_support(config)

    assert first.seed is None and first.unsupported_seed == 7
    assert second.seed is None and second.unsupported_seed == 7
    assert probed == [None, None]  # the probe never sends the refused seed
    err = capsys.readouterr().err
    assert err.count("does not take a seed") == 1


def test_arms_run_still_refuses_a_temperature_the_route_refuses_even_with_a_seed(monkeypatch):
    from dokimi.eval.arms_run import ArmsRunConfig, check_sampling_support

    monkeypatch.setattr("dokimi.llm.sampling_refusals",
                        lambda *a, **k: {"seed": NAME_REFUSAL, "temperature": "only temperature=1"})
    with pytest.raises(ValueError, match="does not support temperature"):
        check_sampling_support(ArmsRunConfig(product="demo", model="m", provider="amazon-bedrock",
                                             temperature=0.0, seed=7))


def test_arms_run_keeps_a_supported_seed(monkeypatch):
    from dokimi.eval import arms_run
    from dokimi.eval.arms_run import ArmsRunConfig, check_sampling_support

    monkeypatch.setattr("dokimi.llm.sampling_refusals", lambda *a, **k: {})
    monkeypatch.setattr(arms_run, "probe_sampling", lambda *a: None)
    config = check_sampling_support(ArmsRunConfig(product="demo", model="m", provider="openai", seed=7))
    assert config.seed == 7 and config.unsupported_seed is None


def test_the_run_config_records_an_unsupported_seed_as_unsupported_not_as_seeded():
    from dokimi.eval.arm_runner import ArmRunner

    unseeded = ArmRunner([BaselineTreatment()], seed=None, unsupported_seed=7)._run_config()
    assert unseeded["seed"] is None
    assert unseeded["seed_requested"] == 7 and unseeded["seed_status"] == "unsupported"

    plain = ArmRunner([BaselineTreatment()], seed=None)._run_config()
    assert "seed_status" not in plain and "seed_requested" not in plain


def test_a_judge_drops_a_seed_the_route_refuses_and_warns_once(monkeypatch, capsys):
    from dokimi.eval import judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", lambda *a, **k: {"seed": NAME_REFUSAL})
    assert judge_sampling.check_judge_sampling("amazon-bedrock", "m", None, 7) is None
    assert judge_sampling.check_judge_sampling("amazon-bedrock", "m", None, 7) is None
    assert capsys.readouterr().err.count("does not take a seed") == 1


def test_a_judge_keeps_a_seed_the_route_takes(monkeypatch):
    from dokimi.eval import judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", lambda *a, **k: {})
    assert judge_sampling.check_judge_sampling("openai", "m", None, 7) == 7
