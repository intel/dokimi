# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pins the request sequence of the judged-reply loops: the prompt of every attempt,
which attempts are asked live and which replay from the response cache, and the cache
keys written. The judge, the panel and the pairwise judge all reprompt, replay and cap
their attempts through one seam, so these sequences must not move with the refactor.

The provider is the scripted :func:`fake_llm` adapter, behind the real response cache.
"""

import threading

import pytest

from dokimi import caching
from dokimi.eval.judge import Judge
from dokimi.eval.panel import JudgeConfig, JudgePanel
from dokimi.eval.rubric import FORMAT_REMINDER
from dokimi.judged import REDRAW_REMINDER, judged_reply
from dokimi.llm import ChatOpenAI, Unparseable

JUDGE_GOOD = (
    '{"correctness": 80, "completeness": 80, "specificity": 80, '
    '"code_quality": 80, "actionability": 80, "aggregate": 0}'
)
PANEL_GOOD = (
    '{"reasoning": "ok", "correctness": 80, "completeness": 80, "specificity": 80, '
    '"code_quality": 80, "actionability": 80}'
)
REMINDER_1 = FORMAT_REMINDER.format(n=1)
REMINDER_2 = FORMAT_REMINDER.format(n=2)


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


def _judge(temperature=None, seed=None):
    judge = Judge.__new__(Judge)
    judge._accumulate_usage = lambda usage: None
    judge.temperature, judge.seed = temperature, seed
    judge._usage_lock, judge._usage_totals = threading.Lock(), {"redrawn_judgements": 0}
    judge.llm = ChatOpenAI(model="gpt-x", api_key="k", temperature=temperature, seed=seed)
    return judge


def _panel_config(pinned):
    if pinned:
        return JudgeConfig(role="technical_expert", model="gpt-x", provider="openai", temperature=0.0, seed=7)
    return JudgeConfig(role="technical_expert", model="gpt-x", provider="openai")


def _cache_files(cache_dir):
    return sorted(p.name for p in cache_dir.glob("*.json"))


# ── Judge ─────────────────────────────────────────────────────────────────────

def test_judge_unpinned_redraws_the_plain_prompt(cache_dir, fake_llm):
    fake_llm.push("not json", JUDGE_GOOD)
    judge = _judge()
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    base = fake_llm.prompts[0]
    assert fake_llm.prompts == [base, base]
    assert judge._usage_totals["redrawn_judgements"] == 1
    # The rerun replays the stored draw-1 reply: no request, and the same cache key.
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 2
    assert _cache_files(cache_dir) == ["390cb5ec044c966c083f91c205b73e15c845f347fe610b5be560fb4f4b2149a0.json"]


def test_judge_pinned_redraws_with_the_reminder(cache_dir, fake_llm):
    fake_llm.push("not json", JUDGE_GOOD)
    judge = _judge(temperature=0.0, seed=7)
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    base = fake_llm.prompts[0]
    assert fake_llm.prompts == [base, base + REMINDER_1]
    assert judge._usage_totals["redrawn_judgements"] == 1
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 2
    assert _cache_files(cache_dir) == ["f0cd923dc4fd72e7e4746e77505f11ad80878e92ee2190c52df6c64fe78b531f.json"]


def test_judge_pinned_three_unparseable_replies_walk_both_reminders_then_raise(cache_dir, fake_llm):
    fake_llm.push("not json", "still not", "nope")
    judge = _judge(temperature=0.0, seed=7)
    with pytest.raises(Unparseable):
        judge.score_answer(library_name="L", question="q", answer="a")
    base = fake_llm.prompts[0]
    assert fake_llm.prompts == [base, base + REMINDER_1, base + REMINDER_2]
    assert _cache_files(cache_dir) == []
    # Nothing was stored, so a rerun asks all three attempts live again.
    fake_llm.push("not json", "still not", JUDGE_GOOD)
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 6
    assert fake_llm.prompts[3:] == [base, base + REMINDER_1, base + REMINDER_2]


def test_judge_pinned_rerun_replays_the_stored_third_attempt_only(cache_dir, fake_llm):
    fake_llm.push("not json", "still not", JUDGE_GOOD)
    judge = _judge(temperature=0.0, seed=7)
    judge.score_answer(library_name="L", question="q", answer="a")
    assert len(fake_llm.requests) == 3
    assert judge._usage_totals["redrawn_judgements"] == 1
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 3  # attempts 0 and 1 were never stored, attempt 2 replays
    assert judge._usage_totals["redrawn_judgements"] == 2
    assert _cache_files(cache_dir) == ["44466ea735b63f70d52f8968b4a0544ab38ec0445bbb566f49f5e7728d8e8662.json"]


# ── Panel ─────────────────────────────────────────────────────────────────────

def test_panel_unpinned_redraws_the_plain_prompt(cache_dir, fake_llm):
    fake_llm.push("not json", PANEL_GOOD)
    cfg = _panel_config(pinned=False)
    panel = JudgePanel(judges=[cfg])
    vote = panel._call_judge(cfg, "q", "a", "L", "")
    base = panel._build_prompt(cfg.role, "q", "a", "L", "")
    assert (vote.error, vote.aggregate) == (None, 80.0)
    assert fake_llm.prompts == [base, base]
    assert panel._call_judge(cfg, "q", "a", "L", "").aggregate == 80.0
    assert len(fake_llm.requests) == 2
    assert _cache_files(cache_dir) == ["0e8b211bc84281d2c458fcf8fa7f256181cda39a46b40a37186d51595d8d5579.json"]


def test_panel_pinned_redraws_with_the_reminder(cache_dir, fake_llm):
    fake_llm.push("not json", PANEL_GOOD)
    cfg = _panel_config(pinned=True)
    panel = JudgePanel(judges=[cfg])
    vote = panel._call_judge(cfg, "q", "a", "L", "")
    base = panel._build_prompt(cfg.role, "q", "a", "L", "")
    assert (vote.error, vote.aggregate) == (None, 80.0)
    assert fake_llm.prompts == [base, base + REMINDER_1]
    assert panel._call_judge(cfg, "q", "a", "L", "").aggregate == 80.0
    assert len(fake_llm.requests) == 2
    assert _cache_files(cache_dir) == ["50554ebc560c85cc138281ff31f5245b8208cc9f260ced6a82c763814cb7c883.json"]


def test_panel_pinned_three_unparseable_replies_fail_the_vote_and_store_nothing(cache_dir, fake_llm):
    fake_llm.push("not json", "still not", "nope")
    cfg = _panel_config(pinned=True)
    panel = JudgePanel(judges=[cfg])
    vote = panel._call_judge(cfg, "q", "a", "L", "")
    base = panel._build_prompt(cfg.role, "q", "a", "L", "")
    assert vote.error and vote.aggregate is None
    assert fake_llm.prompts == [base, base + REMINDER_1, base + REMINDER_2]
    assert _cache_files(cache_dir) == []


# ── Pairwise ──────────────────────────────────────────────────────────────────

def _pairwise_judge(pinned):
    from dokimi.eval.pairwise import PairwiseJudge

    llm = ChatOpenAI(model="gpt-x", api_key="k", temperature=0.0 if pinned else None, seed=7 if pinned else None)
    return PairwiseJudge(
        ask=lambda prompt, draw=0, cache_if=None: llm.invoke(prompt, draw=draw, cache_if=cache_if).content,
        pinned=pinned,
    )


def test_pairwise_unpinned_redraws_the_plain_prompt(cache_dir, fake_llm):
    fake_llm.push("Verdict:", "A", "B")
    assert _pairwise_judge(pinned=False).compare("L", "q", "x", "y").score == 1
    first, second, third = fake_llm.prompts
    assert second == first  # a new sample of the same prompt is the redraw at free sampling
    assert third != first  # the second order is a different prompt
    assert len(_cache_files(cache_dir)) == 2  # "Verdict:" was not stored


def test_pairwise_pinned_redraws_with_a_reminder(cache_dir, fake_llm):
    """At pinned sampling the same prompt returns the same unparseable reply, so the
    redraw must change the prompt, as the judge and the panel do."""
    fake_llm.push("Verdict:", "A", "B")
    assert _pairwise_judge(pinned=True).compare("L", "q", "x", "y").score == 1
    first, second, third = fake_llm.prompts
    assert second != first and second.startswith(first)
    assert len(fake_llm.requests) == 3


def test_pairwise_pinned_rerun_replays_the_stored_reminder_redraw(cache_dir, fake_llm):
    fake_llm.push("Verdict:", "A", "B")
    _pairwise_judge(pinned=True).compare("L", "q", "x", "y")
    assert len(fake_llm.requests) == 3
    assert _pairwise_judge(pinned=True).compare("L", "q", "x", "y").score == 1
    assert len(fake_llm.requests) == 3  # both orders replay their stored reminder-bearing reply


# ── The seam itself, with a scripted ask ──────────────────────────────────────

def _scripted(*replies):
    """An ``Ask`` that returns *replies* in order and records each (draw, prompt)."""
    asked = []

    def ask(prompt, *, draw=0, cache_if=None):
        asked.append((draw, prompt))
        return replies[len(asked) - 1]

    return ask, asked


def _as_int(text):
    return int(text) if text.isdigit() else None


def test_judged_reply_draws_each_attempt_and_reminds_only_pinned_redraws():
    ask, asked = _scripted("x", "y", "7")
    assert judged_reply(ask, "p", _as_int, pinned=False) == (7, "7")
    assert asked == [(0, "p"), (1, "p"), (2, "p")]

    ask, asked = _scripted("x", "y", "7")
    assert judged_reply(ask, "p", _as_int, pinned=True, draw=1) == (7, "7")
    assert [d for d, _ in asked] == [3, 4, 5]  # draw 1 is attempts 3, 4 and 5
    assert [p for _, p in asked] == ["p", "p" + REDRAW_REMINDER.format(n=1),
                                     "p" + REDRAW_REMINDER.format(n=2)]


def test_judged_reply_reports_the_first_redraw_once():
    calls = []
    ask, _ = _scripted("x", "y", "7")
    assert judged_reply(ask, "p", _as_int, pinned=False, on_redraw=lambda: calls.append(1))[0] == 7
    assert calls == [1]  # attempt 1 is the first redraw; attempt 2 is not counted again
    calls.clear()
    ask, _ = _scripted("7")
    judged_reply(ask, "p", _as_int, pinned=False, on_redraw=lambda: calls.append(1))
    assert calls == []  # answered first time: no redraw
