# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge panel: aggregation rules, per-judge 2×2, vendor and sensitivity warnings, CLI."""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi.commands.novelty import _judge_refs, _role_models
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.measure import BASELINE_ARM, SelfJudgeError, run_measurement
from dokimi.novelty.panel import aggregate, panel_record, panel_warnings, shared_vendors
from dokimi.novelty.probes import Probe

CC, CMQ, PC = "correct_complete", "correct_missing_qualifier", "partially_correct"
WO, WR, AB = "wrong_other", "wrong_reversed", "abstained"


@pytest.mark.parametrize("votes, rule, want", [
    ([CC, CMQ, WO], "median", CMQ),
    ([CC, WO, WO], "median", WO),
    ([CC, CC, WO, WO], "median", WO),          # an even panel rounds toward wrong
    ([WR, WR, WO, CC, CC], "median", WR),      # the most common wrong verdict survives
    ([CC, PC, WO], "majority", PC),            # correct majority: lower median of the correct votes
    ([CC, WO, AB, AB], "majority", AB),
    ([CC, CMQ, CC], "unanimous", CMQ),         # all correct: the lowest correct vote
    ([CC, CC, WR], "unanimous", WR),
    ([], "median", None),
])
def test_aggregate_rules(votes, rule, want):
    assert aggregate(votes, rule) == want


def test_aggregate_rejects_unknown_rule():
    with pytest.raises(ValueError, match="rule"):
        aggregate([CC], "mean")


def test_shared_vendors_names_only_repeated_families():
    assert shared_vendors(["gpt-6-luna", "gpt-6-sol", "claude-sonnet-5", "kimi-k3"]) == {
        "openai": ["gpt-6-luna", "gpt-6-sol"]}


def _judge(model: str, verdict_for):
    def transport(prompt, system):
        return json.dumps({"verdict": verdict_for(prompt)}), None
    return LLMClient(model=model, transport=transport)


def _setup():
    atoms = [Atom(statement=f"fact {i}", question=f"Q{i}?", answer="yes", span_ids=("s0001",))
             for i in range(4)]
    probes = [Probe(atom_id=a.id, kind="direct", question=a.question, expected=a.answer)
              for a in atoms]

    def answer(prompt, system):
        return ("ARTIFACT" if "Reference material" in prompt else "BARE") + "\nconfidence: 0.5", None

    return atoms, probes, LLMClient(model="answerer", transport=answer)


def test_panel_grades_with_every_judge_and_keeps_each_verdict():
    atoms, probes, answerer = _setup()
    # lenient judge: everything correct; strict judges: only the with-artifact answer
    lenient = _judge("gpt-x", lambda p: CC)
    strict_a = _judge("claude-y", lambda p: CC if "RESPONSE: ARTIFACT" in p else WO)
    strict_b = _judge("kimi-z", lambda p: CC if "RESPONSE: ARTIFACT" in p else WO)
    result = run_measurement(atoms, probes, "the artifact", answerer=answerer, judge=lenient,
                             panel=(strict_a, strict_b), aggregate="median", concurrency=1)
    assert all(set(r.judge_verdicts) == {"gpt-x", "claude-y", "kimi-z"} for r in result.runs)
    assert result.paired("lenient")["added"] == 1.0  # the panel sides with the two strict judges
    rec = panel_record(result, "median")
    assert rec["judges"] == ["gpt-x", "claude-y", "kimi-z"]
    assert rec["per_judge_paired"]["gpt-x"]["lenient"]["added"] == 0.0
    assert rec["per_judge_paired"]["kimi-z"]["lenient"]["added"] == 1.0
    assert rec["added_share_range"]["lenient"] == 1.0
    assert rec["judge_sensitive"] is True
    assert any("judge-sensitive" in w for w in panel_warnings(rec))
    assert "judge_verdicts" in result.runs[0].as_dict()


def test_single_judge_run_records_no_panel():
    atoms, probes, answerer = _setup()
    result = run_measurement(atoms, probes, "t", answerer=answerer,
                             judge=_judge("j", lambda p: CC), concurrency=1)
    assert result.runs[0].judge_verdicts is None
    assert "judge_verdicts" not in result.runs[0].as_dict()
    assert panel_record(result, "median") is None


def test_a_judge_that_fails_is_left_out_of_the_vote():
    atoms, probes, answerer = _setup()
    broken = _judge("broken", lambda p: None)
    result = run_measurement(atoms[:1], probes[:1], "t", answerer=answerer,
                             judge=_judge("a", lambda p: WO), panel=(broken, _judge("b", lambda p: WO)),
                             concurrency=1)
    base = next(r for r in result.runs if r.arm == BASELINE_ARM)
    assert base.verdict == WO
    assert base.judge_verdicts["broken"] == "error"


def test_every_panel_judge_is_checked_against_the_answerer():
    atoms, probes, answerer = _setup()
    with pytest.raises(SelfJudgeError):
        run_measurement(atoms, probes, "t", answerer=answerer, judge=_judge("j", lambda p: CC),
                        panel=(answerer,))


def _args(**kw):
    base = dict(judges=None, judge_model=None, judge_provider=None, model="m", provider="litellm",
                extract_model=None, extract_provider=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_judge_flag_parses_provider_model_and_sets_the_extractor():
    args = _args(judges=["openrouter/gpt-6-luna", "openrouter/claude-sonnet-5"])
    assert _judge_refs(args) == [("gpt-6-luna", "openrouter"), ("claude-sonnet-5", "openrouter")]
    roles = _role_models(args)
    assert roles["judge"] == ("gpt-6-luna", "openrouter")
    assert roles["extractor"] == ("gpt-6-luna", "openrouter")


@pytest.mark.parametrize("kw, match", [
    ({"judges": ["gpt-6-luna"]}, "PROVIDER/MODEL"),
    ({"judges": ["nope/x"]}, "PROVIDER/MODEL"),
    ({"judges": ["openrouter/a", "openrouter/a"]}, "twice"),
    ({"judges": ["openrouter/a", "openai/a"]}, "twice"),
    ({"judges": ["openrouter/a"], "judge_model": "b"}, "one form"),
])
def test_judge_flag_rejects_bad_refs(kw, match):
    with pytest.raises(ValueError, match=match):
        _judge_refs(_args(**kw))
