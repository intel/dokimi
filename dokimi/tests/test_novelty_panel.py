# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge panel: aggregation rules, per-judge 2×2, vendor and sensitivity warnings, CLI."""

from __future__ import annotations

import argparse
import json

import pytest

from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.cost import judge_refs as _judge_refs
from dokimi.novelty.cost import role_models as _role_models
from dokimi.novelty.judge2 import check_judge2
from dokimi.novelty.measure import (
    BASELINE_ARM,
    MeasureResult,
    ProbeRun,
    SelfJudgeError,
    run_measurement,
)
from dokimi.novelty.panel import aggregate, panel_warnings, shared_vendors
from dokimi.novelty.panel_record import panel_record
from dokimi.novelty.probes import Probe
from dokimi.utils import InputError

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


def test_two_judges_of_one_vendor_the_vendor_map_lacks_are_one_family():
    grok = ["x-ai/grok-4.6", "x-ai/grok-4"]
    assert shared_vendors(grok) == {"xai": grok}
    assert shared_vendors(["acme/one", "acme/two"]) == {}  # two unrecognised judges share nothing


def test_judge_flag_rejects_one_model_under_two_spellings():
    args = _args(judges=["openai/gpt-4o", "openrouter/openai/gpt-4o", "anthropic/claude-sonnet-5"])
    with pytest.raises(ValueError, match="twice"):
        _judge_refs(args)


def test_run_measurement_rejects_one_model_given_twice_before_any_call():
    atoms, probes, answerer = _setup()
    calls = []
    a = _judge("gpt-4o", lambda p: calls.append(p) or CC)
    b = _judge("openai/gpt-4o", lambda p: calls.append(p) or CC)
    with pytest.raises(InputError, match="same model as another judge"):
        run_measurement(atoms, probes, "t", answerer=answerer, judge=a, panel=(b,))
    assert calls == []


def test_judge2_is_checked_against_every_panel_judge():
    _, _, answerer = _setup()
    first, second = _judge("j-one", lambda p: CC), _judge("j-two", lambda p: CC)
    with pytest.raises(InputError, match="panel judge"):
        check_judge2(_judge("j-two", lambda p: CC), answerer, 0.5, False, first, (second,))
    check_judge2(_judge("j-three", lambda p: CC), answerer, 0.5, False, first, (second,))


def _run(atom: str, arm: str, verdicts: dict[str, str]) -> ProbeRun:
    return ProbeRun(atom_id=atom, probe_kind="direct", arm=arm, question=f"Q{atom}?",
                    response="r", verdict=next(iter(verdicts.values())), judge_verdicts=verdicts)


def test_added_range_is_taken_on_the_pairs_every_judge_graded():
    err = "error"
    runs = [
        _run("a", BASELINE_ARM, {"j1": WO, "j2": WO}), _run("a", "full", {"j1": CC, "j2": err}),
        _run("b", BASELINE_ARM, {"j1": CC, "j2": CC}), _run("b", "full", {"j1": CC, "j2": CC}),
    ]
    rec = panel_record(MeasureResult(runs=runs, arms=(BASELINE_ARM, "full")), "median")
    assert rec["per_judge_paired"]["j1"]["lenient"]["added"] == 0.5
    assert rec["per_judge_paired"]["j2"]["lenient"]["added"] == 0.0  # its own sample lost pair a
    assert rec["added_share_range"]["lenient"] == 0.0
    assert rec["judge_sensitive"] is False


def test_a_range_of_exactly_the_threshold_is_not_judge_sensitive():
    runs = []
    for i in range(10):  # j1 sees one probe gained, j2 none: a range of exactly 0.10
        runs += [_run(str(i), BASELINE_ARM, {"j1": WO, "j2": WO}),
                 _run(str(i), "full", {"j1": CC if i == 0 else WO, "j2": WO})]
    rec = panel_record(MeasureResult(runs=runs, arms=(BASELINE_ARM, "full")), "median")
    assert rec["added_share_range"]["lenient"] == 0.1
    assert rec["judge_sensitive"] is False


def test_estimate_counts_each_panel_judges_grading_calls():
    from dokimi.novelty.cost import add_panel_calls

    estimate = {"by_role": {"extractor": 3, "answerer": 8, "judge": 8}, "probe_calls": 32,
                "total_calls": 40, "total_calls_if_every_pair_escalates": 44,
                "expected_calls": 19}
    add_panel_calls(_args(judges=["openai/a", "openai/b", "anthropic/c"]), estimate)
    assert estimate["by_role"]["judge"] == 24
    assert (estimate["probe_calls"], estimate["total_calls"], estimate["expected_calls"]) == (48, 56, 35)
    assert estimate["total_calls_if_every_pair_escalates"] == 60
    single = dict(estimate, by_role=dict(estimate["by_role"]))
    add_panel_calls(_args(judges=["openai/a"]), single)
    assert single == estimate


def test_estimate_only_refuses_an_unusable_judge(tmp_path):
    from dokimi.cli import build_parser

    (tmp_path / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n\n# S\n\nText.\n",
                                       encoding="utf-8")
    args = build_parser().parse_args(["skill", "novelty", "analyze", "--artifact",
                                      f"skill:{tmp_path}", "--estimate-only", "--judge", "nope/x"])
    with pytest.raises(InputError, match="PROVIDER/MODEL"):
        args.func(args)


def test_the_report_names_the_panel_and_analyze_saves_each_verdict(tmp_path):
    from dokimi.commands.novelty_run import save_panel_verdicts
    from dokimi.novelty.report import _judged_by

    runs = [_run("a", BASELINE_ARM, {"j1": WO, "j2": CC}), _run("a", "full", {"j1": CC, "j2": CC})]
    result = type("R", (), {"measure_result": MeasureResult(runs=runs)})()
    out = tmp_path / "novelty.json"
    dest = save_panel_verdicts(result, out)
    assert dest == tmp_path / "novelty.judge_verdicts.json"
    assert json.loads(dest.read_text())[0]["judge_verdicts"] == {"j1": WO, "j2": CC}
    assert "panel of 2 judges" in _judged_by({"judge_model": "j1", "judge_panel":
                                              {"judges": ["j1", "j2"], "rule": "median"}})
    assert _judged_by({"judge_model": "j1"}) == "**j1**"
    single = type("R", (), {"measure_result": MeasureResult(
        runs=[ProbeRun("a", "direct", "full", "q", "r", WO)])})()
    assert save_panel_verdicts(single, out) is None


def _capped_judge(model: str, verdict: str, *, priced: bool, truncated: bool = False):
    from dokimi.metrics.usage import UsageRecord

    def transport(prompt, system):
        usage = UsageRecord(cost_usd=1.0 if priced else None, truncated=truncated)
        return json.dumps({"verdict": verdict}), usage

    from dokimi.novelty.client import SpendCap
    return LLMClient(model=model, transport=transport, cap=SpendCap(limit_usd=100.0))


def _one_probe():
    atoms, probes, answerer = _setup()
    return atoms[:1], probes[:1], answerer


def test_an_unpriced_judge_stopping_a_panel_does_not_decide_the_verdict():
    atoms, probes, answerer = _one_probe()
    first = _capped_judge("j1", WO, priced=True)
    second = _capped_judge("j2", CC, priced=False)
    result = run_measurement(atoms, probes, "t", answerer=answerer, judge=first, panel=(second,),
                             aggregate="median", concurrency=1)
    stopped = [r for r in result.runs if r.judge_verdicts and "j2" not in r.judge_verdicts]
    assert stopped and all(r.verdict == "error" for r in stopped)
    assert result.ungraded == len(stopped)
    assert result.paired("lenient")["added"] == 0.0


def test_a_truncated_judge_reply_under_a_cap_is_a_failed_grade():
    atoms, probes, answerer = _one_probe()
    judge = _capped_judge("j", CC, priced=False, truncated=True)
    result = run_measurement(atoms, probes, "t", answerer=answerer, judge=judge, concurrency=1)
    assert all(r.verdict == "error" for r in result.runs)
    assert result.ungraded == len(result.runs)
