# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-turn session measurement: history kept across turns, usage per turn.

Every test runs against the ``fake_llm`` adapter, so nothing reaches a provider.
"""

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from conftest import DEMO_SKILL_MARKER
from dokimi.eval.arm_runner import ANSWER_PROMPT_PLAIN, ArmRunner
from dokimi.eval.arm_summary import cost_summary
from dokimi.llm import Reply
from dokimi.metrics.usage import UsageRecord
from dokimi.skills import load_skill
from dokimi.treatments import BaselineTreatment, SkillTreatment
from dokimi.treatments.base import AgentConfig, Tool, Treatment

SESSION_Q = {
    "id": "q1",
    "question": "How do I parallelize a loop?",
    "follow_ups": ["Now with a reduction?", "What does it cost?"],
}


def _roles(messages):
    return [m["role"] for m in messages]


def test_session_sends_the_full_history_each_turn(fake_llm):
    fake_llm.push("a1", "a2", "a3")
    runner = ArmRunner([BaselineTreatment()], session_turns=3)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    reqs = fake_llm.requests
    assert [_roles(r.messages) for r in reqs] == [
        ["user"],
        ["user", "assistant", "user"],
        ["user", "assistant", "user", "assistant", "user"],
    ]
    # The opening is the plain single-turn prompt; follow-ups are sent as written.
    assert reqs[0].messages[0]["content"] == ANSWER_PROMPT_PLAIN.format(
        question="How do I parallelize a loop?")
    assert reqs[1].messages[1]["content"] == "a1"
    assert reqs[2].messages[2]["content"] == "Now with a reduction?"
    assert reqs[2].messages[3]["content"] == "a2"
    assert reqs[2].messages[4]["content"] == "What does it cost?"

    arm = records[0]["arms"]["baseline"]
    assert arm["answer"] == "a3"
    assert [t["answer"] for t in arm["session"]["turns"]] == ["a1", "a2", "a3"]
    assert arm["session"]["stopped_reason"] == "completed"
    assert records[0]["session_turns"] == 3


def test_skill_context_is_in_the_opening_turn_only(fake_llm, demo_skill):
    fake_llm.push("a1", "a2")
    runner = ArmRunner([SkillTreatment(load_skill(demo_skill))], session_turns=2)

    runner.run("oneTBB", [SESSION_Q], concurrency=1)

    first, second = fake_llm.requests
    assert DEMO_SKILL_MARKER in first.messages[-1]["content"]
    assert DEMO_SKILL_MARKER not in second.messages[-1]["content"]


def test_per_turn_usage_with_cache_tokens_is_recorded(fake_llm):
    fake_llm.push(
        Reply(text="a1", usage=UsageRecord(prompt_tokens=100, completion_tokens=10,
                                           total_tokens=110, cache_write_tokens=60,
                                           cost_usd=0.001)),
        Reply(text="a2", usage=UsageRecord(prompt_tokens=180, completion_tokens=12,
                                           total_tokens=192, cache_read_tokens=60,
                                           cost_usd=0.002)),
        Reply(text="a3", usage=UsageRecord(prompt_tokens=200, completion_tokens=9,
                                           total_tokens=209, cache_read_tokens=160,
                                           cost_usd=0.003)),
    )
    runner = ArmRunner([BaselineTreatment()], session_turns=3)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    turns = arm["session"]["turns"]
    assert [t["metrics"]["prompt_tokens"] for t in turns] == [100, 180, 200]
    assert [t["metrics"]["completion_tokens"] for t in turns] == [10, 12, 9]
    assert [t["metrics"]["cache_read_tokens"] for t in turns] == [0, 60, 160]
    assert [t["metrics"]["cache_write_tokens"] for t in turns] == [60, 0, 0]
    assert turns[1]["metrics"]["cost_usd"] == pytest.approx(0.002)

    # The arm-level totals are the sum over turns, read by the usual rollups.
    assert arm["token_usage"] == {"prompt_tokens": 480, "completion_tokens": 31,
                                  "total_tokens": 511}
    assert arm["metrics"]["n_calls"] == 3
    assert arm["metrics"]["cache_read_tokens"] == 220
    summary = cost_summary(records)["baseline"]
    assert summary["total_llm_calls"] == 3
    assert summary["total_cost_usd"] == pytest.approx(0.006)
    assert summary["cache_hit_ratio"] == pytest.approx(220 / 480, abs=1e-4)


def test_reminder_arm_repeats_the_reminder_on_every_user_turn(fake_llm):
    fake_llm.push("a1", "a2", "b1", "b2")
    runner = ArmRunner([BaselineTreatment()], session_turns=2, turn_reminder="BE TERSE")

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    assert set(records[0]["arms"]) == {"baseline", "baseline+reminder"}
    assert runner.arm_names == ["baseline", "baseline+reminder"]
    plain, reminded = fake_llm.requests[:2], fake_llm.requests[2:]
    for req in plain:
        for m in req.messages:
            if m["role"] == "user":
                assert "system-reminder" not in m["content"]
    for req in reminded:
        users = [m["content"] for m in req.messages if m["role"] == "user"]
        assert all(u.endswith("\n\n<system-reminder>BE TERSE</system-reminder>") for u in users)
        # Only user turns carry the reminder; the history is what the model said.
        assert all("system-reminder" not in m["content"]
                   for m in req.messages if m["role"] == "assistant")
    assert records[0]["arms"]["baseline+reminder"]["session"]["reminder"] is True
    assert records[0]["arms"]["baseline"]["session"]["reminder"] is False


def test_sessions_are_not_judged(fake_llm):
    fake_llm.push("a1", "a2")
    runner = ArmRunner([BaselineTreatment()], session_turns=2)
    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    with pytest.raises(ValueError, match="measurement-only"):
        runner.judge(MagicMock(), "oneTBB", records)


def test_a_question_short_of_follow_ups_fails_before_any_answer_is_paid(fake_llm):
    runner = ArmRunner([BaselineTreatment()], session_turns=3)
    short = {"id": "q9", "question": "Short?", "follow_ups": ["only one"]}

    with pytest.raises(ValueError, match="q9.*needs 2"):
        runner.run("oneTBB", [SESSION_Q, short], concurrency=1)
    assert fake_llm.requests == []


def test_an_empty_reply_ends_the_session(fake_llm):
    fake_llm.push("", "never sent")
    runner = ArmRunner([BaselineTreatment()], session_turns=2)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    assert len(fake_llm.requests) == 1
    assert arm["session"]["stopped_reason"] == "empty_reply"
    assert len(arm["session"]["turns"]) == 1
    assert arm["answer"] == ""


def test_a_failing_turn_keeps_the_completed_turns(fake_llm):
    fake_llm.push("a1", ValueError("boom"))
    runner = ArmRunner([BaselineTreatment()], session_turns=3)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    assert arm["answer"] is None
    assert arm["error"] == "generation_failed: turn 1: boom"
    assert arm["session"]["stopped_reason"] == "error"
    assert [t["answer"] for t in arm["session"]["turns"]] == ["a1"]
    assert len(fake_llm.requests) == 2


class _Noop(Tool):
    name = "noop"
    description = "Does nothing."

    def call(self, **kwargs) -> str:
        return "ok"


class _AgenticTreatment(Treatment):
    name = "agentic"

    def prepare(self, question_text, library_name, library_id=None):
        return AgentConfig(tools=[_Noop()])


def test_an_agentic_arm_is_refused_in_a_session(fake_llm):
    runner = ArmRunner([_AgenticTreatment()], session_turns=2)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["agentic"]
    assert arm["error"].startswith("session_unsupported")
    assert arm["answer"] is None
    assert fake_llm.requests == []


def test_session_options_are_validated_up_front():
    with pytest.raises(ValueError, match="at least 2"):
        ArmRunner([BaselineTreatment()], session_turns=1)
    with pytest.raises(ValueError, match="needs session_turns"):
        ArmRunner([BaselineTreatment()], turn_reminder="BE TERSE")
    with pytest.raises(ValueError, match="needs session_turns"):
        ArmRunner([BaselineTreatment()], session_turns=2, turn_reminder="   ")


def test_the_artifact_names_the_session_settings(fake_llm):
    fake_llm.push("a1", "a2")
    runner = ArmRunner([BaselineTreatment()], session_turns=2, turn_reminder="BE TERSE")
    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    out = runner.build_output("oneTBB", records)

    assert out["session"] == {"turns": 2, "turn_reminder": "BE TERSE"}
    assert out["arms"] == ["baseline", "baseline+reminder"]


def test_plain_runs_are_unchanged_by_the_session_fields(fake_llm):
    fake_llm.push("answer")
    runner = ArmRunner([BaselineTreatment()])
    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    out = runner.build_output("oneTBB", records)

    assert "session" not in out
    assert "session_turns" not in records[0]
    assert "session" not in records[0]["arms"]["baseline"]


def test_the_cli_refuses_judge_with_session_before_any_answer(fake_llm):
    from dokimi.commands.arms import _build_arms_output

    args = Namespace(judge=True, session_turns=2, turn_reminder=None)
    with pytest.raises(ValueError, match="measurement-only"):
        _build_arms_output(args, ["baseline"], [SESSION_Q])
    assert fake_llm.requests == []
