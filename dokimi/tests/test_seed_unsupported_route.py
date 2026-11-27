# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A seed the route does not take is warned about once, dropped, and recorded as unsupported.

The run is not refused: the seed was never sent, so the artifact must not read as seeded.
A temperature the route refuses still refuses the run.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from dokimi.llm_sampling import NAME_REFUSAL, warn_seed_dropped
from dokimi.treatments import BaselineTreatment


@pytest.fixture(autouse=True)
def _fresh_warning(monkeypatch):
    monkeypatch.setattr("dokimi.llm_sampling._warned_seed_routes", set())


def test_concurrent_warning_is_emitted_once_per_route(monkeypatch):
    from dokimi import llm_sampling
    from tests.waiting import wait_for_event

    first_writing, release_first = threading.Event(), threading.Event()
    messages = []

    def print_warning(message, *, file):
        messages.append(message)
        if not first_writing.is_set():
            first_writing.set()
            wait_for_event(release_first, 10, what="test releases first warning")

    monkeypatch.setattr(llm_sampling, "print", print_warning, raising=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(warn_seed_dropped, "m", "amazon-bedrock")
        try:
            wait_for_event(first_writing, 10, what="first warning enters stderr write")
            pool.submit(warn_seed_dropped, "m", "amazon-bedrock").result(timeout=10)
            pool.submit(warn_seed_dropped, "other", "amazon-bedrock").result(timeout=10)
        finally:
            release_first.set()
        first.result(timeout=10)
    assert len(messages) == 2
    assert "model 'm'" in messages[0] and "model 'other'" in messages[1]


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



def test_runs_that_differ_only_in_the_dropped_seed_are_replicates_not_rival_models():
    from dokimi.report.model_compare import check_run_consistency, replicate_key
    from dokimi.utils import InputError

    def run(**cfg):
        return {"model": "m", "provider": "amazon-bedrock", "judge_model": "j",
                "judge_provider": "openai", "baseline_arm": "baseline", "arms": ["baseline", "skill:s"],
                "run_config": {"temperature": None, **cfg}}

    first = run(seed=None, seed_requested=1, seed_status="unsupported")
    second = run(seed=None, seed_requested=2, seed_status="unsupported")
    assert replicate_key(first) == replicate_key(second)
    with pytest.raises(InputError, match="replicates of one measurement"):
        check_run_consistency([("br1", first), ("br2", second)], "golden")
    assert replicate_key(first) != replicate_key(run(seed=1))


def test_a_judge_drops_a_seed_the_route_refuses_and_warns_once(monkeypatch, capsys):
    from dokimi.eval import judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", lambda *a, **k: {"seed": NAME_REFUSAL})
    assert judge_sampling.check_judge_sampling("amazon-bedrock", "m", None, 7) is None
    assert judge_sampling.check_judge_sampling("amazon-bedrock", "m", None, 7) is None
    assert capsys.readouterr().err.count("does not take a seed") == 1


def test_judge_sampling_defers_route_check_until_the_model_is_known(monkeypatch):
    from dokimi.eval import judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", _sampling_by_route)
    assert judge_sampling.check_judge_sampling("amazon-bedrock", None, None, 7) == 7
    assert judge_sampling.check_judge_sampling("amazon-bedrock", "m", None, 7) is None


def test_a_judge_keeps_a_seed_the_route_takes(monkeypatch):
    from dokimi.eval import judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", lambda *a, **k: {})
    assert judge_sampling.check_judge_sampling("openai", "m", None, 7) == 7


_JUDGE_REPLY = json.dumps({
    "correctness": 80, "completeness": 80, "specificity": 80,
    "actionability": 80, "code_quality": 80, "reasoning": "sound", "aggregate": 80,
})
_QUESTION = {"id": "q", "question": "How do I run it?"}
_ANSWER = dict(_QUESTION, without_docs={"answer": "Run the command."})


def test_panel_sends_only_supported_seeds_and_records_omissions(fake_llm, monkeypatch, tmp_path):
    from dokimi.eval import judge_sampling
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    monkeypatch.setattr(judge_sampling, "sampling_refusals", _sampling_by_route)
    refused = JudgeConfig("technical_expert", "m", "amazon-bedrock", seed=7)
    supported = JudgeConfig("doc_reviewer", "m", "openai", seed=9)
    panel = JudgePanel(judges=[refused, supported])
    fake_llm.respond = lambda _: _JUDGE_REPLY
    scored = panel.score_answer(library_name="demo", question="How?", answer="Like this.")
    output = tmp_path / "panel.json"
    panel.evaluate_answers([_ANSWER], "demo", output_path=output)

    assert sorted((req.provider, req.seed) for req in fake_llm.requests) == [
        ("amazon-bedrock", None), ("amazon-bedrock", None), ("openai", 9), ("openai", 9),
    ]
    assert scored["aggregate"] == 80.0
    saved = json.loads(output.read_text())["evaluations"][0]["without_docs"]
    for result in (scored["panel"], saved):
        votes = {vote["role"]: vote for vote in result["votes"]}
        assert votes["technical_expert"]["seed_requested"] == 7
        assert votes["technical_expert"]["seed_status"] == "unsupported"
        assert "seed_status" not in votes["doc_reviewer"]
    assert refused.seed == 7 and supported.seed == 9


@pytest.mark.parametrize("provider, sent", [("amazon-bedrock", None), ("openai", 7)])
def test_arms_execution_reaches_provider_and_records_the_effective_seed(fake_llm, monkeypatch, provider, sent):
    from dokimi import llm
    from dokimi.eval.arms_run import ArmsRunConfig, probe_sampling, run_arms

    monkeypatch.setattr(llm, "sampling_refusals", _sampling_by_route)
    probe_sampling.cache_clear()
    fake_llm.respond = lambda _: "Run the command."
    config = ArmsRunConfig(product="demo", model="seed-test-arm", provider=provider, seed=7)
    try:
        output = run_arms(config, ["baseline"], [_QUESTION])
    finally:
        probe_sampling.cache_clear()

    requests = [req for req in fake_llm.requests if req.max_tokens != 16]
    assert [req.seed for req in requests] == [sent]
    assert output["answers"][0]["arms"]["baseline"]["answer"] == "Run the command."
    record = output["run_config"]
    assert record["seed"] == sent
    if sent is None:
        assert (record["seed_requested"], record["seed_status"]) == (7, "unsupported")
    else:
        assert "seed_requested" not in record and "seed_status" not in record


@pytest.mark.parametrize("provider, sent", [("amazon-bedrock", None), ("openai", 7)])
def test_judge_execution_reaches_provider_and_saves_seed_provenance(fake_llm, monkeypatch, tmp_path, provider, sent):
    from dokimi.eval import Judge, judge_sampling

    monkeypatch.setattr(judge_sampling, "sampling_refusals", _sampling_by_route)
    judge = Judge(model="seed-test-judge", provider=provider, seed=7)
    fake_llm.respond = lambda _: _JUDGE_REPLY
    score = judge.score_answer(library_name="demo", question="How?", answer="Like this.")
    output = tmp_path / "eval.json"
    judge.evaluate_answers("demo", [_ANSWER], output_path=output)

    assert [req.seed for req in fake_llm.requests] == [sent, sent]
    assert score["aggregate"] == 80.0
    artifact = json.loads(output.read_text())
    assert artifact["evaluations"][0]["without_docs"]["aggregate"] == 80.0
    if sent is None:
        assert (artifact["judge_seed_requested"], artifact["judge_seed_status"]) == (7, "unsupported")
    else:
        assert "judge_seed_requested" not in artifact and "judge_seed_status" not in artifact


def _sampling_by_route(model, provider, *, temperature, seed):
    return {"seed": NAME_REFUSAL} if provider == "amazon-bedrock" and seed is not None else {}
