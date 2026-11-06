# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Provenance stamp, judge/answerer vendors, the second-judge subset, paired reversal resistance."""

import argparse
import json

import pytest

from dokimi.commands.novelty_run import judge2_client, stamp
from dokimi.novelty import provenance as prov
from dokimi.novelty.client import LLMClient
from dokimi.novelty.controls import reversal_by_arm, reversal_paired
from dokimi.novelty.judge2 import agreement, cohen_kappa, regrade
from dokimi.novelty.measure import MeasureResult, ProbeRun
from dokimi.novelty.probes import Probe
from dokimi.utils import InputError

RIGHT, WRONG, REVERSED = "correct_complete", "wrong_other", "wrong_reversed"


@pytest.mark.parametrize("model, vendor", [
    ("us.anthropic.claude-opus-5-5", "anthropic"), ("opus", "anthropic"),
    ("gpt-6-luna", "openai"), ("grok-5", "xai"), ("gemini-3-pro", "google"),
    ("nvidia/nemotron-ultra", "nvidia"), ("llama-4-maverick", "meta"), ("mistral-large", "unknown"),
])
def test_vendor_is_read_off_the_model_id(model, vendor):
    assert prov.vendor_of(model) == vendor


def test_same_vendor_warns_and_unknown_vendors_never_match():
    same = prov.vendor_record("claude-haiku-5", "us.anthropic.claude-opus-5-5")
    assert same == {"answerer_vendor": "anthropic", "judge_vendor": "anthropic", "same_vendor": True}
    assert "anthropic" in prov.same_vendor_warning(same)
    assert prov.vendor_record("mistral-a", "mistral-b")["same_vendor"] is False
    assert prov.same_vendor_warning(prov.vendor_record("gpt-6", "claude-opus-5")) is None


def test_provenance_survives_a_missing_git(monkeypatch):
    monkeypatch.setattr(prov, "git_commit", lambda: None)
    block = prov.provenance(artifact_digest="sha256:abc", answerer_model="gpt-6",
                            judge_model="claude-opus-5", context_framing="neutral")
    assert block["git_commit"] is None
    assert block["artifact_digest"] == "sha256:abc"
    assert block["prompts_sha256"].startswith("sha256:")
    assert (block["context_framing"], block["same_vendor"]) == ("neutral", False)


def _rev(atom, arm, verdict):
    return ProbeRun(atom_id=atom, probe_kind="reversal", arm=arm, question=f"q{atom}",
                    response="r", verdict=verdict)


def test_reversal_resistance_is_reported_per_arm_and_paired():
    runs = [_rev("a", "no_artifact", REVERSED), _rev("a", "full", WRONG),
            _rev("b", "no_artifact", REVERSED), _rev("b", "full", WRONG),
            _rev("c", "no_artifact", WRONG), _rev("c", "full", REVERSED),
            _rev("d", "no_artifact", WRONG), _rev("d", "full", WRONG)]
    assert reversal_by_arm(runs, ("no_artifact", "full")) == {"no_artifact": 0.5, "full": 0.75}
    paired = reversal_paired(runs, baseline_arm="no_artifact", with_arm="full")
    assert (paired["resisted_only_with"], paired["resisted_only_without"]) == (2, 1)
    assert (paired["resisted_both"], paired["resisted_neither"], paired["n_paired"]) == (1, 0, 4)
    assert paired["difference"] == 0.25
    result = MeasureResult(runs=runs, arms=("no_artifact", "full"))
    assert result.reversal_resisted() == 0.75
    assert reversal_paired(runs[:1], baseline_arm="no_artifact", with_arm="full") is None


def test_cohen_kappa_and_agreement():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohen_kappa([True, True], [True, True]) is None
    stats = agreement([(RIGHT, "partially_correct"), (WRONG, WRONG), (RIGHT, WRONG), (WRONG, WRONG)])
    assert stats["n_compared"] == 4
    assert stats["lenient_agreement"] == 0.75
    assert stats["exact_agreement"] == 0.5
    assert stats["kappa_lenient"] == 0.5
    assert agreement([])["kappa_lenient"] is None


def _measured(n):
    probes = [Probe(atom_id=f"a{i}", kind="direct", question=f"q{i}", expected="e") for i in range(n)]
    runs = [ProbeRun(atom_id=p.atom_id, probe_kind="direct", arm="full", question=p.question,
                     response="r", verdict=RIGHT) for p in probes]
    return MeasureResult(runs=runs, arms=("no_artifact", "full"), probes=probes)


def test_regrade_samples_the_fraction_and_compares_with_the_second_judge():
    replies = iter([RIGHT, WRONG, RIGHT])
    judge2 = LLMClient(model="gpt-6",
                       transport=lambda p, s: (json.dumps({"verdict": next(replies)}), None))
    out = regrade(_measured(20), judge2, frac=0.15, concurrency=1)
    assert (out["n_sampled"], out["n_compared"], out["vendor"]) == (3, 3, "openai")
    assert out["exact_agreement"] == round(2 / 3, 4)
    assert out["lenient_agreement"] == round(2 / 3, 4)
    again = regrade(_measured(20), LLMClient(model="g", transport=lambda p, s: ("{}", None)), frac=0.15)
    assert (again["n_compared"], again["n_errored"], again["exact_agreement"]) == (0, 3, None)


def _args(**kw):
    base = dict(judge2="m-answer", judge2_provider=None, judge2_frac=0.15, allow_self_judge=False,
                context_framing="reference", seed=0, concurrency=1)
    return argparse.Namespace(**{**base, **kw})


def _make(model, provider, role, args):
    return LLMClient(model=model, provider=provider, role=role)


def test_judge2_equal_to_the_answerer_is_refused_unless_allowed():
    answerer, judge = LLMClient(model="m-answer"), LLMClient(model="m-judge", provider="p")
    with pytest.raises(InputError, match="answering model"):
        judge2_client(_args(), answerer, judge, _make)
    allowed = judge2_client(_args(allow_self_judge=True), answerer, judge, _make)
    assert (allowed.model, allowed.provider, allowed.role) == ("m-answer", "p", "judge2")
    with pytest.raises(InputError, match="judge2-frac"):
        judge2_client(_args(judge2="other", judge2_frac=0), answerer, judge, _make)
    assert judge2_client(_args(judge2=None), answerer, judge, _make) is None


def test_stamp_adds_provenance_to_a_probe_record_and_warns_on_one_vendor(capsys):
    payload = {"ref_digest": "sha256:x"}
    stamp(payload, _args(context_framing="neutral"), _measured(1),
          answerer=LLMClient(model="claude-haiku-5"), judge=LLMClient(model="claude-opus-5"))
    block = payload["provenance"]
    assert (block["artifact_digest"], block["context_framing"]) == ("sha256:x", "neutral")
    assert block["same_vendor"] is True and "judge2" not in payload
    assert "both anthropic models" in capsys.readouterr().err
