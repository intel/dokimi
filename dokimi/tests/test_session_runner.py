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


def test_an_empty_reply_ends_the_session_and_is_a_failed_arm_that_keeps_its_spend(fake_llm):
    fake_llm.push("a1", "", "never sent")
    runner = ArmRunner([BaselineTreatment()], session_turns=3)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    assert len(fake_llm.requests) == 2
    assert arm["session"]["stopped_reason"] == "empty_reply"
    assert len(arm["session"]["turns"]) == 2
    # Not a success: its totals would cover fewer turns than the other questions' rows.
    assert arm["error"] == "empty_reply: turn 1"
    assert arm["answer"] is None
    assert arm["metrics"]["n_calls"] == 2
    assert cost_summary(records)["baseline"]["total_llm_calls"] == 2


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


def test_a_failed_session_still_counts_the_turns_already_paid_for(fake_llm):
    fake_llm.push(Reply(text="a1", usage=UsageRecord(prompt_tokens=100, completion_tokens=10,
                                                     total_tokens=110, cost_usd=0.01)),
                  ValueError("boom"))
    runner = ArmRunner([BaselineTreatment()], session_turns=3)

    records = runner.run("oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    assert arm["token_usage"]["total_tokens"] == 110
    summary = cost_summary(records)["baseline"]
    assert summary["total_llm_calls"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.01)


def test_a_failed_session_does_not_skew_the_answer_length_comparison(fake_llm):
    # Control: q1 answers 100 chars at turn 0 and then returns an empty reply, q2 succeeds.
    # Plugin: both succeed. Every real answer is 100 chars on both sides.
    questions = [SESSION_Q, {**SESSION_Q, "id": "q2"}]
    fake_llm.push("x" * 100, "", "x" * 100, "x" * 100, "x" * 100)
    control = ArmRunner([BaselineTreatment()], session_turns=2)
    control_records = control.run("oneTBB", questions, concurrency=1)
    failed = control_records[0]["arms"]["baseline"]
    assert failed["error"] == "empty_reply: turn 1"
    assert "raw_answer_chars" not in failed["metrics"] and "final_answer_chars" not in failed["metrics"]
    assert failed["metrics"]["n_calls"] == 2  # the spend stays visible to the cost rollup

    fake_llm.push("x" * 100, "x" * 100, "x" * 100, "x" * 100)
    plugin_records = ArmRunner([BaselineTreatment()], session_turns=2).run(
        "oneTBB", questions, concurrency=1)

    def artifact(records, plugin_set):
        out = _artifact(session_turns=2, plugin_set=plugin_set)
        out.update(answers=records, total_questions=2)
        return out

    from dokimi.eval.plugin_delta import _answer_text_deltas

    rows = _answer_text_deltas(artifact(control_records, "none"), artifact(plugin_records, "caveman"),
                               ["baseline"])["baseline"]
    for key in ("raw_answer_chars", "final_answer_chars"):
        assert rows[key] == {"baseline_avg": 100.0, "plugin_avg": 100.0, "delta": 0.0}


def test_a_session_that_fails_on_its_first_turn_has_no_spend_to_report(fake_llm):
    fake_llm.push(ValueError("boom"))
    records = ArmRunner([BaselineTreatment()], session_turns=2).run(
        "oneTBB", [SESSION_Q], concurrency=1)
    arm = records[0]["arms"]["baseline"]
    assert "metrics" not in arm and "token_usage" not in arm


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


class _SystemPromptTreatment(Treatment):
    name = "sys"

    def prepare(self, question_text, library_name, library_id=None):
        return AgentConfig(system_prompt="You are terse.")


class _BigContextTreatment(Treatment):
    name = "big"

    def prepare(self, question_text, library_name, library_id=None):
        return AgentConfig(injected_context=[{"content": "\u00a7" * 40_000, "source": "s"}])


def test_every_request_of_a_session_carries_the_sampling_options_and_system_prompt(fake_llm):
    fake_llm.push("a1", "a2", "a3")
    runner = ArmRunner([_SystemPromptTreatment()], session_turns=3, temperature=0.3, seed=7,
                       max_tokens=50)

    runner.run("oneTBB", [SESSION_Q], concurrency=1)

    assert len(fake_llm.requests) == 3
    for req in fake_llm.requests:
        assert (req.temperature, req.seed, req.max_tokens) == (0.3, 7, 50)
        assert req.messages[0] == {"role": "system", "content": "You are terse."}


def test_the_opening_turn_caps_injected_context_as_the_plain_run_does(fake_llm):
    from dokimi.eval.arm_runner import MAX_CONTEXT_CHARS

    fake_llm.push("a1", "a2")
    ArmRunner([_BigContextTreatment()], session_turns=2).run(
        "oneTBB", [SESSION_Q], concurrency=1)

    opening = fake_llm.requests[0].messages[-1]["content"]
    assert opening.count("\u00a7") == MAX_CONTEXT_CHARS
    # The context arm opens with the first question, not a follow-up.
    assert SESSION_Q["question"] in opening
    assert not any(follow_up in opening for follow_up in SESSION_Q["follow_ups"])


def test_opening_is_prepared_from_the_first_turn_not_the_last(fake_llm):
    seen = []

    class _Spy(Treatment):
        name = "spy"

        def prepare(self, question_text, library_name, library_id=None):
            seen.append(question_text)
            return AgentConfig()

    fake_llm.push("a1", "a2", "a3")
    ArmRunner([_Spy()], session_turns=3).run("oneTBB", [SESSION_Q], concurrency=1)
    assert seen == [SESSION_Q["question"]]


def test_output_shapers_apply_to_the_session_answer_but_not_the_raw_one(fake_llm):
    from dokimi.plugins import create_plugins, plugin_set_metadata, wrap_treatments

    plugins = create_plugins(["truncate:5"])
    treatments = wrap_treatments([BaselineTreatment()], plugins)
    fake_llm.push("a1", "a long final answer")
    runner = ArmRunner(treatments, session_turns=2, plugin_set=plugin_set_metadata(plugins))

    arm = runner.run("oneTBB", [SESSION_Q], concurrency=1)[0]["arms"]["baseline"]

    assert arm["raw_answer"] == "a long final answer"
    assert arm["answer"] != arm["raw_answer"] and len(arm["answer"]) == 5


def _parse_arms_run(argv):
    from dokimi.cli import build_parser

    return build_parser().parse_args(
        ["arms", "run", "--product", "oneTBB", "--questions", "q.json",
         "--arms", "baseline"] + argv)


def test_arms_run_wires_the_session_flags_into_the_run(fake_llm, monkeypatch):
    import dokimi.commands.arms as arms_mod
    from dokimi.commands.arms import _build_arms_output

    monkeypatch.setattr(arms_mod, "_check_sampling_support", lambda args: None)
    fake_llm.push("a1", "a2", "r1", "r2")
    args = _parse_arms_run(["--session-turns", "2", "--turn-reminder", "BE TERSE"])

    out = _build_arms_output(args, ["baseline"], [SESSION_Q])

    assert out["session"] == {"turns": 2, "turn_reminder": "BE TERSE"}
    assert out["arms"] == ["baseline", "baseline+reminder"]
    assert out["run_config"]["session_turns"] == 2
    assert len(fake_llm.requests) == 4


def test_arms_run_without_session_flags_runs_single_turn(fake_llm, monkeypatch):
    import dokimi.commands.arms as arms_mod
    from dokimi.commands.arms import _build_arms_output

    monkeypatch.setattr(arms_mod, "_check_sampling_support", lambda args: None)
    fake_llm.push("a1")
    out = _build_arms_output(_parse_arms_run([]), ["baseline"], [SESSION_Q])
    assert "session" not in out and "session_turns" not in out["run_config"]


def test_session_flags_parse_to_ints_and_text():
    args = _parse_arms_run(["--session-turns", "3", "--turn-reminder", "X"])
    assert (args.session_turns, args.turn_reminder) == (3, "X")
    assert _parse_arms_run([]).session_turns is None


def _artifact(session_turns=None, plugin_set="none", reminder=None):
    out = {"schema_version": "arms.v1", "library_name": "L", "model": "m", "provider": "p",
           "harness": "h", "arms": ["baseline"], "baseline_arm": "baseline",
           "total_questions": 1, "plugin_set": plugin_set, "answers": [],
           "run_config": {"max_tokens": None}}
    if session_turns is not None:
        out["run_config"]["session_turns"] = session_turns
        out["run_config"]["turn_reminder"] = reminder
    return out


def test_a_session_run_is_not_paired_with_a_single_turn_run():
    from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs

    with pytest.raises(PluginDeltaError, match="different session_turns: None vs 3"):
        compare_plugin_runs(_artifact(), _artifact(session_turns=3, plugin_set="caveman"))


def test_a_run_with_a_turn_reminder_is_not_paired_with_one_without():
    from dokimi.eval.matrix_rollup import _pair_key
    from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs

    # Same turn count: only the request text differs, on every user turn.
    with pytest.raises(PluginDeltaError, match="different turn_reminder: None vs 'R'"):
        compare_plugin_runs(_artifact(3, reminder=None), _artifact(3, reminder="R", plugin_set="caveman"))
    assert _pair_key(_artifact(3, reminder=None)) != _pair_key(_artifact(3, reminder="R"))


def test_the_matrix_pairing_key_separates_session_runs():
    from dokimi.eval.matrix_rollup import _pair_key

    assert _pair_key(_artifact()) != _pair_key(_artifact(session_turns=3))
    assert _pair_key(_artifact(session_turns=3)) == _pair_key(_artifact(session_turns=3))


def test_the_run_config_records_the_session_settings_only_when_set(fake_llm):
    assert "session_turns" not in ArmRunner([BaselineTreatment()])._run_config()
    config = ArmRunner([BaselineTreatment()], session_turns=2, turn_reminder="R")._run_config()
    assert (config["session_turns"], config["turn_reminder"]) == (2, "R")


def test_a_long_skill_keeps_the_plain_runs_cache_breakpoint_in_every_turn(fake_llm, tmp_path):
    from dokimi.caching import MIN_CACHEABLE_PREFIX_CHARS

    d = tmp_path / "big-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: big-skill\ndescription: d\n---\n"
                                + "x" * MIN_CACHEABLE_PREFIX_CHARS, encoding="utf-8")
    model = {"model": "claude-sonnet-5-5", "provider": "anthropic"}
    fake_llm.push("p1", "a1", "a2")
    ArmRunner([SkillTreatment(load_skill(d))], **model).run("oneTBB", [SESSION_Q], concurrency=1)
    ArmRunner([SkillTreatment(load_skill(d))], session_turns=2, **model).run(
        "oneTBB", [SESSION_Q], concurrency=1)

    plain, first, second = fake_llm.requests
    assert first.messages == plain.messages
    assert second.messages[0] == plain.messages[0]
    assert plain.messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


# "Why?" has no space: a sentence would fail the blank check even if the list check went.
@pytest.mark.parametrize("follow_ups", ["Why?", ["ok", "   "], [1]])
def test_follow_ups_must_be_a_list_of_non_blank_strings(fake_llm, follow_ups):
    runner = ArmRunner([BaselineTreatment()], session_turns=2)
    bad = {"id": "q9", "question": "Q?", "follow_ups": follow_ups}

    with pytest.raises(ValueError, match="q9: 'follow_ups' must be a list of non-blank strings"):
        runner.run("oneTBB", [bad], concurrency=1)
    assert fake_llm.requests == []


def test_a_whitespace_reply_ends_the_session_as_an_empty_one(fake_llm):
    fake_llm.push("a1", "   ", "never sent")
    records = ArmRunner([BaselineTreatment()], session_turns=3).run(
        "oneTBB", [SESSION_Q], concurrency=1)

    arm = records[0]["arms"]["baseline"]
    assert len(fake_llm.requests) == 2
    assert (arm["error"], arm["session"]["stopped_reason"]) == ("empty_reply: turn 1", "empty_reply")
