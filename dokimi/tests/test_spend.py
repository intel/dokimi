# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The cost and usage accumulators of an LLM run: every field each one writes (#229 P2-7).

The same calls go through each accumulator, so a difference between their rules shows as a
difference here: a call with no price, a call replayed from the response cache, a call that
returned no usage, and no call at all.
"""

from __future__ import annotations

import itertools

import pytest

from dokimi.eval import Judge
from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.arm_summary import cost_summary
from dokimi.metrics.usage import UsageRecord, roll_up_by_role
from dokimi.novelty.client import LLMClient, roll_up_cost
from dokimi.spend import Spend, SpendCap, SpendCapReached
from dokimi.treatments import BaselineTreatment

PRICED = UsageRecord(prompt_tokens=10, completion_tokens=2, total_tokens=12,
                     cache_read_tokens=4, cache_write_tokens=1, cost_usd=0.25)
PRICED_2 = UsageRecord(prompt_tokens=5, completion_tokens=1, total_tokens=6, cost_usd=0.125)
UNPRICED = UsageRecord(prompt_tokens=3, completion_tokens=1, total_tokens=4)
REPLAYED = UsageRecord(prompt_tokens=7, completion_tokens=7, total_tokens=14, cost_usd=1.0,
                       replayed=True)
_IDS = itertools.count()


def _client(*rows: UsageRecord | None, role: str = "judge") -> LLMClient:
    replies = iter(rows)
    client = LLMClient(model=f"m-{role}", provider="openai", role=role,
                       transport=lambda prompt, system: ("x", next(replies)))
    for _ in rows:
        client.text("p")
    return client


def _client_summary(*rows: UsageRecord | None) -> dict:
    summary = _client(*rows).cost_summary()
    assert (summary["model"], summary["provider"], summary["role"]) == ("m-judge", "openai", "judge")
    return {k: v for k, v in summary.items() if k not in ("model", "provider", "role")}


def _novelty(calls, input_tokens, output_tokens, cache_read, cache_write, replayed, usd) -> dict:
    return {"llm_calls": calls, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
            "replayed_calls": replayed, "usd": usd}


# ── novelty: LLMClient.cost_summary and roll_up_cost, the rule the others are to follow ──

def test_a_novelty_client_sums_every_priced_call():
    assert _client_summary(PRICED, PRICED_2) == _novelty(2, 15, 3, 4, 1, 0, 0.375)


def test_one_unpriced_call_leaves_a_novelty_client_with_no_dollar_total():
    assert _client_summary(PRICED, UNPRICED) == _novelty(2, 13, 3, 4, 1, 0, None)


def test_a_call_with_no_usage_leaves_a_novelty_client_with_no_dollar_total():
    assert _client_summary(PRICED, None) == _novelty(2, 10, 2, 4, 1, 0, None)


def test_a_replayed_call_adds_no_tokens_and_no_dollars_to_a_novelty_client():
    assert _client_summary(PRICED, REPLAYED) == _novelty(2, 10, 2, 4, 1, 1, 0.25)
    assert _client_summary(REPLAYED) == _novelty(1, 0, 0, 0, 0, 1, 0.0)
    assert _client_summary(UsageRecord(replayed=True)) == _novelty(1, 0, 0, 0, 0, 1, 0.0)


def test_a_novelty_client_with_no_call_has_no_dollar_total():
    assert _client_summary() == _novelty(0, 0, 0, 0, 0, 0, None)


def test_the_novelty_roll_up_sums_the_roles_and_keeps_each_row():
    answerer, judge = _client(PRICED, REPLAYED, role="answerer"), _client(PRICED_2, role="judge")
    total = roll_up_cost([answerer, judge])
    assert total == {**_novelty(3, 15, 3, 4, 1, 1, 0.375),
                     "by_role": [answerer.cost_summary(), judge.cost_summary()]}


def test_one_role_without_a_dollar_total_leaves_the_roll_up_without_one():
    total = roll_up_cost([_client(PRICED, role="answerer"), _client(UNPRICED, role="judge")])
    assert {k: v for k, v in total.items() if k != "by_role"} == _novelty(2, 13, 3, 4, 1, 0, None)
    assert roll_up_cost([])["usd"] is None


def test_a_role_that_made_no_call_adds_nothing_to_the_roll_up():
    """A run stopped before the judge's first call still says what its other roles spent."""
    total = roll_up_cost([_client(PRICED, role="answerer"), _client(role="judge")])
    assert (total["llm_calls"], total["usd"]) == (1, 0.25)
    assert [row["usd"] for row in total["by_role"]] == [0.25, None]


# ── eval: Judge judge_metrics, written by evaluate and by arms run ──────────────────────

def _judge(*rows: object, redrawn: int = 0) -> Judge:
    judge = Judge(model="m", provider="openai")
    for row in rows:
        judge._accumulate_usage(row)
    judge._redrawn_judgements = redrawn
    return judge


def _judge_metrics(calls, known, usd, prompt, completion, total, redrawn=0, replayed=0) -> dict:
    return {"calls": calls, "replayed_calls": replayed, "cost_known_calls": known, "cost_usd": usd,
            "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
            "redrawn_judgements": redrawn}


def _both_judge_records(judge: Judge) -> dict:
    """``judge_metrics`` as ``evaluate`` writes it, after checking ``arms run`` writes the same."""
    evaluate = judge._build_evaluation_output([])["judge_metrics"]
    arms = ArmRunner([BaselineTreatment()]).build_output("lib", [], [], judge=judge)["judge_metrics"]
    assert arms == evaluate
    return evaluate


def test_the_judge_sums_every_priced_call():
    assert _both_judge_records(_judge(PRICED, PRICED_2, redrawn=2)) == _judge_metrics(
        2, 2, 0.375, 15, 3, 18, redrawn=2)


def test_the_judge_shows_the_priced_part_when_a_call_is_unpriced():
    assert _both_judge_records(_judge(PRICED, UNPRICED)) == _judge_metrics(2, 1, 0.25, 13, 3, 16)
    assert _both_judge_records(_judge(UNPRICED)) == _judge_metrics(1, 0, None, 3, 1, 4)


def test_a_replayed_judge_call_is_counted_apart_and_not_billed():
    assert _both_judge_records(_judge(PRICED, REPLAYED)) == _judge_metrics(
        2, 2, 0.25, 10, 2, 12, replayed=1)
    assert _both_judge_records(_judge(REPLAYED)) == _judge_metrics(1, 1, 0.0, 0, 0, 0, replayed=1)


def test_a_judge_call_with_no_usage_record_is_a_call_with_no_price():
    assert _both_judge_records(_judge(PRICED, None, {"cost_usd": 1.0})) == _judge_metrics(
        3, 1, 0.25, 10, 2, 12)


def test_a_judge_with_no_call_has_no_dollar_total():
    assert _both_judge_records(_judge()) == _judge_metrics(0, 0, None, 0, 0, 0)


# ── eval: the answers' cost_summary per arm (arms run) ─────────────────────────────────

def _row(**metrics: object) -> dict:
    return {"question_id": f"q{next(_IDS)}", "arms": {"skill": {"metrics": metrics}}}


def test_the_arm_cost_summary_sums_every_field():
    rows = [
        _row(**PRICED.with_latency(2.0, 0.5).as_metrics_dict()),
        _row(**(PRICED_2 + PRICED_2).with_latency(1.0).as_metrics_dict()),
        {"question_id": "no-metrics", "arms": {"skill": {"answer": "a"}}},
    ]
    assert cost_summary(rows) == {"skill": {
        "n": 2, "total_cost_usd": 0.5, "cost_known_n": 3, "total_llm_calls": 3,
        "prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24,
        "mean_latency_sec": 1.5, "mean_ttft_sec": 0.5, "cache_hit_ratio": 0.2,
    }}


def test_the_arm_cost_summary_shows_the_priced_part_when_a_call_is_unpriced():
    rows = [_row(**PRICED.as_metrics_dict()), _row(**UNPRICED.as_metrics_dict()),
            _row(**(PRICED_2 + UNPRICED).as_metrics_dict())]
    summary = cost_summary(rows)["skill"]
    assert (summary["total_cost_usd"], summary["cost_known_n"], summary["total_llm_calls"]) == (
        0.375, 2, 4)
    unpriced = cost_summary([_row(**UNPRICED.as_metrics_dict())])["skill"]
    assert (unpriced["total_cost_usd"], unpriced["cost_known_n"]) == (None, 0)


def test_the_arm_cost_summary_reads_a_row_written_before_cost_known_calls():
    summary = cost_summary([_row(cost_usd=0.5, n_calls=2, total_tokens=0, prompt_tokens=0)])["skill"]
    assert (summary["total_cost_usd"], summary["cost_known_n"], summary["cache_hit_ratio"]) == (
        0.5, 2, None)


# ── metrics: roll_up_by_role, main agent against subagents ─────────────────────────────

def test_the_role_roll_up_shows_each_bucket_s_priced_part_and_no_total_while_one_is_unpriced():
    rows = [PRICED, UsageRecord(total_tokens=6, cost_usd=0.125, role="subagent"),
            UsageRecord(total_tokens=4, role="subagent")]
    assert roll_up_by_role(rows) == {
        "main_agent_tokens": 12, "subagent_tokens": 10, "full_system_tokens": 22,
        "main_agent_cost": 0.25, "subagent_cost": 0.125, "full_system_cost": None,
        "full_system_cost_complete": False,
    }


def test_the_role_roll_up_leaves_a_replayed_call_out_and_an_empty_bucket_complete():
    assert roll_up_by_role([PRICED, REPLAYED]) == {
        "main_agent_tokens": 12, "subagent_tokens": 0, "full_system_tokens": 12,
        "main_agent_cost": 0.25, "subagent_cost": None, "full_system_cost": 0.25,
        "full_system_cost_complete": True,
    }


# ── the rule itself: dokimi.spend ───────────────────────────────────────────────────────

def test_a_call_in_flight_when_another_stopped_the_cap_is_still_recorded():
    """``--concurrency``: only the unpriced call raises; the run's next call is refused."""
    cap = SpendCap(limit_usd=5.0)
    with pytest.raises(SpendCapReached, match="no price is known for a"):
        cap.record(UNPRICED, "a")
    cap.record(PRICED, "b")
    assert (cap.calls, cap.spent_usd, cap.usd) == (2, 0.25, None)
    with pytest.raises(SpendCapReached, match="the run stopped on a call with no known price"):
        cap.before_call("c")


def test_a_row_that_counts_priced_calls_without_a_price_is_unpriced():
    row = UsageRecord(n_calls=2, cost_known_calls=2)
    spend = Spend.of([PRICED, row])
    assert (spend.calls, spend.priced_calls, spend.known_usd, spend.usd) == (3, 1, 0.25, None)


def test_the_cap_counts_one_call_per_record_whatever_its_row_says():
    """``record`` meters one call: a row of ``n_calls=0`` with no price still stops the run."""
    cap = SpendCap(limit_usd=1.0)
    cap.record(UsageRecord(n_calls=3, cost_usd=0.25), "a")
    with pytest.raises(SpendCapReached, match="no price is known for b"):
        cap.record(UsageRecord(n_calls=0), "b")
    assert (cap.calls, cap.unpriced_calls, cap.spent_usd) == (2, 1, 0.25)
    client = _client(UsageRecord(n_calls=2, cost_usd=0.5))
    assert (client.cost_summary()["llm_calls"], client.cost_summary()["usd"]) == (1, 0.5)


def test_a_row_claiming_more_priced_calls_than_it_has_hides_no_other_unpriced_call():
    over = UsageRecord(n_calls=1, cost_known_calls=2, cost_usd=0.25)
    assert Spend.of([over, UNPRICED]).usd is None


def test_the_cap_refuses_the_call_after_spend_reaches_the_limit_exactly():
    cap = SpendCap(limit_usd=0.5)
    cap.record(PRICED, "a")
    cap.before_call("b")
    cap.record(PRICED, "b")
    with pytest.raises(SpendCapReached, match=r"spend cap reached: \$0\.50 of \$0\.50 after 2 calls"):
        cap.before_call("c")
