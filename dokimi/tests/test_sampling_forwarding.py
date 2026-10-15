# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Sampling params reach every request path and key the response cache."""

from types import SimpleNamespace

import pytest

import dokimi.llm as llm_mod
from dokimi import caching
from dokimi.utils import get_llm


def _resp(text="ok"):
    message = SimpleNamespace(content=text, tool_calls=None, role="assistant")
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


@pytest.fixture
def requests(monkeypatch):
    import litellm

    sent = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: sent.append(kw) or _resp())
    return sent


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


SETTINGS = [{"temperature": 0.0}, {"temperature": 1.0}, {}, {"temperature": 0.0, "seed": 1},
            {"temperature": 0.0}]


def test_llm_call_cache_keys_on_temperature_and_seed(cache_dir, requests):
    for sampling in SETTINGS:
        llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k",
                                    **sampling)
    assert [{k: r[k] for k in ("temperature", "seed") if k in r} for r in requests] == SETTINGS[:4]


def test_get_llm_cache_keys_on_temperature_and_seed(cache_dir, requests):
    for sampling in SETTINGS:
        get_llm("amazon-bedrock", "us.anthropic.x", "k", **sampling).invoke("ask")
    assert [{k: r[k] for k in ("temperature", "seed") if k in r} for r in requests] == SETTINGS[:4]


def test_chat_completion_forwards_sampling(requests):
    llm_mod.chat_completion([{"role": "user", "content": "q"}], model="gpt-6.1-sol",
                            provider="openai", api_key="k", temperature=0.0, seed=3)
    llm_mod.chat_completion([{"role": "user", "content": "q"}], model="gpt-6.1-sol",
                            provider="openai", api_key="k")
    assert (requests[0]["temperature"], requests[0]["seed"]) == (0.0, 3)
    assert "temperature" not in requests[1] and "seed" not in requests[1]


def test_run_agent_loop_forwards_sampling(requests):
    from dokimi.eval.agent_runner import run_agent_loop

    out = run_agent_loop("q", [], model="gpt-6.1-sol", provider="openai", api_key="k",
                         temperature=0.0, seed=3)
    assert out["answer"] == "ok"
    assert (requests[0]["temperature"], requests[0]["seed"]) == (0.0, 3)


def test_agentic_arm_forwards_sampling(requests, demo_skill):
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.skills import load_skill
    from dokimi.treatments import SkillAgentTreatment

    runner = ArmRunner([SkillAgentTreatment(load_skill(demo_skill))], model="gpt-6.1-sol",
                       provider="openai", temperature=0.0, seed=3)
    records = runner.run("oneTBB", [{"id": "q1", "question": "Q?"}], concurrency=1)
    assert records[0]["arms"]["skill_agent:demo-skill"]["answer"] == "ok"
    assert requests and all((r["temperature"], r["seed"]) == (0.0, 3) for r in requests)


def test_streaming_omits_unset_sampling_and_keys_the_cache_on_it(cache_dir, monkeypatch):
    import litellm

    sent = []

    def stream(**kw):
        sent.append(kw)
        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))],
                                     usage=None)])

    monkeypatch.setattr(litellm, "completion", stream)
    for sampling in SETTINGS:
        llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k",
                                    stream=True, **sampling)
    assert [{k: r[k] for k in ("temperature", "seed") if k in r} for r in sent] == SETTINGS[:4]
