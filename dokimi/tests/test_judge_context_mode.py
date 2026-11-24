# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--judge-context baseline``: the judge is shown no treatment material on any arm.

The answering path is unchanged; only what the judge is given differs, so the judged arms
are scored on the same context as a baseline arm (the control an uplift study needs).
"""

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.cli import build_parser
from dokimi.commands.arms import run_config_from_args
from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.arms_run import ArmsRunConfig, prepare_arms
from dokimi.eval.judge_context import NO_CONTEXT, PROTOCOL, PROTOCOL_MIXED, JudgeContextStore
from dokimi.skills import load_skill
from dokimi.treatments import BaselineTreatment, SkillTreatment
from dokimi.utils import InputError

QUESTIONS = [{"id": "q1", "question": "How do I parallelize a loop?", "category": "usage"}]
#: The stamp a run writes when the judge was shown no material on any arm (it pools with no other protocol).
PROTOCOL_WITHHELD = "treatment_material.withheld.v1"


class _CapturingJudge:
    model = "ctx-judge"
    provider = "fake-provider"

    def __init__(self):
        self.contexts = []

    def score_answer(self, library_name, question, answer, context="", ground_truth=None):
        self.contexts.append(context)
        return {"aggregate": 70, "correctness": 70}


@pytest.fixture
def fake_answers(monkeypatch):
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)


def _run_and_judge(demo_skill, **kwargs):
    """A baseline arm and a skill arm, answered then judged in order: the judge sees [baseline, skill]."""
    runner = ArmRunner([BaselineTreatment(), SkillTreatment(load_skill(demo_skill))], **kwargs)
    records = runner.run("oneTBB", QUESTIONS, concurrency=1)
    judge = _CapturingJudge()
    evaluations = runner.judge(judge, "oneTBB", records)
    return runner, records, judge, evaluations


def test_baseline_mode_shows_the_judge_no_material_on_any_arm(fake_answers, demo_skill):
    _, _, judge, _ = _run_and_judge(demo_skill, judge_context_mode="baseline")
    assert judge.contexts == [NO_CONTEXT, NO_CONTEXT]


def test_default_mode_still_shows_the_judge_the_skill_material(fake_answers, demo_skill):
    _, _, judge, _ = _run_and_judge(demo_skill)
    assert judge.contexts[0] == NO_CONTEXT  # the baseline arm has no material
    assert judge.contexts[1] != NO_CONTEXT  # the skill arm's body and manifest


def test_a_withheld_run_is_stamped_apart_from_a_material_run(fake_answers, demo_skill):
    runner, records, judge, evaluations = _run_and_judge(demo_skill, judge_context_mode="baseline")
    withheld = runner.build_output("oneTBB", records, evaluations, judge=judge)
    assert withheld["judge_context"] == PROTOCOL_WITHHELD

    runner, records, judge, evaluations = _run_and_judge(demo_skill)
    assert runner.build_output("oneTBB", records, evaluations, judge=judge)["judge_context"] == PROTOCOL


@pytest.mark.parametrize(("saved_mode", "new_mode", "stamp"), [
    ("baseline", "arm", PROTOCOL_WITHHELD), ("arm", "baseline", PROTOCOL),
])
def test_rejudging_uses_the_stored_context_protocol(fake_answers, demo_skill, saved_mode, new_mode, stamp):
    runner, records, judge, evaluations = _run_and_judge(demo_skill, judge_context_mode=saved_mode)
    saved = runner.build_output("oneTBB", records, evaluations, judge=judge)
    later = ArmRunner(runner.treatments, judge_context_mode=new_mode)
    later.judge_context = JudgeContextStore.from_artifact(saved)
    second = _CapturingJudge()
    rescored = later.judge(second, "oneTBB", saved["answers"])
    assert second.contexts == judge.contexts
    assert later.build_output("oneTBB", saved["answers"], rescored, judge=second)["judge_context"] == stamp


def test_a_withheld_stamp_conflicting_with_the_saved_material_is_refused(fake_answers, demo_skill):
    runner, records, judge, evaluations = _run_and_judge(demo_skill)
    saved = runner.build_output("oneTBB", records, evaluations, judge=judge)
    saved["judge_context"] = PROTOCOL_WITHHELD
    with pytest.raises(InputError, match="withheld.*non-empty"):
        JudgeContextStore.from_artifact(saved)


def test_missing_withheld_material_still_counts_as_a_fallback(fake_answers, demo_skill):
    runner, records, judge, evaluations = _run_and_judge(demo_skill, judge_context_mode="baseline")
    saved = runner.build_output("oneTBB", records, evaluations, judge=judge)
    del saved["answers"][0]["arms"][runner.treatments[1].name]["judge_context_sha256"]
    later = ArmRunner(runner.treatments)
    later.judge_context = JudgeContextStore.from_artifact(saved)
    rescored = later.judge(judge, "oneTBB", saved["answers"])
    output = later.build_output("oneTBB", saved["answers"], rescored, judge=judge)
    assert output["judge_context"] == PROTOCOL_MIXED


def test_an_unknown_judge_context_mode_is_refused(demo_skill):
    with pytest.raises(ValueError, match="judge_context_mode"):
        ArmRunner([BaselineTreatment()], judge_context_mode="none")


def test_the_flag_defaults_to_the_arm_material_and_reaches_the_runner(fake_answers):
    assert ArmsRunConfig(product="oneTBB").judge_context == "arm"
    config = ArmsRunConfig(product="oneTBB", judge=True, judge_context="baseline")
    prepared = prepare_arms(config, ["baseline"], QUESTIONS)
    assert prepared.runner.judge_context_mode == "baseline"


def test_a_withheld_run_without_a_judge_is_refused():
    # Without a judge nothing is scored, and the answers artifact would keep the withheld text in
    # place of the material, so a later arm-context judge of it would have none.
    with pytest.raises(ValueError, match="needs --judge"):
        ArmsRunConfig(product="oneTBB", judge_context="baseline")


def test_matrix_run_refuses_the_flag_without_judge_as_an_input_error(capsys):
    from dokimi.commands.arms import cmd_arms_matrix_run
    from dokimi.utils import InputError

    args = build_parser().parse_args(["arms", "matrix-run", "--arms", "baseline", "--product", "oneTBB",
                                      "--questions", "q.json", "--matrix-cells", "cells.json",
                                      "--judge-context", "baseline"])
    with pytest.raises(InputError, match="needs --judge"):
        cmd_arms_matrix_run(args)


def test_the_flag_without_judge_exits_before_any_answer():
    command = ["arms", "run", "--arms", "baseline", "--product", "oneTBB", "--questions", "q.json"]
    with pytest.raises(ValueError, match="needs --judge"):
        run_config_from_args(build_parser().parse_args(command + ["--judge-context", "baseline"]))


@pytest.mark.parametrize("command", [
    ["arms", "run", "--arms", "baseline"],
    ["arms", "matrix-run", "--arms", "baseline", "--matrix-cells", "cells.json"],
])
def test_the_flag_is_on_arms_run_and_matrix_run(command):
    base = ["--product", "oneTBB", "--questions", "q.json"]
    default = run_config_from_args(build_parser().parse_args(command + base))
    assert default.judge_context == "arm"
    given = run_config_from_args(build_parser().parse_args(
        command + base + ["--judge", "--judge-context", "baseline"]))
    assert given.judge_context == "baseline"


def test_the_flag_rejects_a_value_outside_its_choices(capsys):
    argv = ["arms", "run", "--product", "oneTBB", "--questions", "q.json", "--arms", "baseline",
            "--judge-context", "none"]
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
