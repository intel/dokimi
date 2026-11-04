# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What one judge and one panel grading call sends and keeps (#229 P1-4).

``Judge._judge_answer`` and ``JudgePanel._call_judge`` each ran their own prompt,
call, parse, redraw and cache loop. Both now run ``judged.judged_reply``, over ``llm.ask_parsed``. These tests
pin what the loop must keep, byte for byte: each prompt sent (by digest), its draw,
the ``cache_if`` verdict, the sampling, the replay probes, the response-cache
keys, the number of provider calls, the format reminder, the parsed output, the
usage totals and the error a failed grade reports.

The expected values were recorded from the loops as they were before the move.
They are a snapshot of that behaviour, not a reading of the new code.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from dokimi import caching, llm
from dokimi.eval.judge import Judge
from dokimi.eval.panel import JudgeConfig, JudgePanel

# A reply both loops accept. Its dimensions differ, so each aggregate is a
# weighted mean of real numbers and not a constant.
REPLY = json.dumps(
    {
        "reasoning": "r",
        "correctness": 90,
        "completeness": 70,
        "specificity": 60,
        "code_quality": 80,
        "actionability": 50,
        "factual_grounding": 100,
        "aggregate": 0,
    }
)
BAD = "not json"

# Expected calls, one tuple per ``LLM.complete``: (prompt sha256[:12], draw,
# format reminder appended, cache_if verdicts on REPLY and BAD, temperature, seed).
JUDGE_PROMPT = "3e6f5add33df"
JUDGE_REMINDER_1 = "08d28853f2cd"
JUDGE_REMINDER_2 = "a546c6810c16"
GROUNDED_PROMPT = "836982fca2c4"
PANEL_PROMPT = "9a06bdb8e367"
PANEL_REMINDER_1 = "14cf96506f77"
PANEL_REMINDER_2 = "011877fae888"
NO_JSON = "No JSON object found in LLM response: 'nope'"
PINNED = {"temperature": 0.0, "seed": 7}


@pytest.fixture
def sent(monkeypatch):
    """Every ``LLM.complete`` a grader makes, in order, as the loop sent it.

    A call the response cache answers is recorded too, and so is a replay probe
    that misses: the loop asked for it.
    """
    calls: list = []
    original = llm.LLM.complete

    def spy(self, prompt, **kw):
        cache_if = kw.get("cache_if")
        verdicts = None if cache_if is None else (cache_if(REPLY), cache_if(BAD))
        calls.append((
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
            kw.get("draw", 0),
            "Reminder (re-draw" in prompt,
            verdicts,
            self.temperature,
            self.seed,
        ))
        return original(self, prompt, **kw)

    monkeypatch.setattr(llm.LLM, "complete", spy)
    return calls


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


def _judge(temperature=None, seed=None) -> Judge:
    return Judge(model="gpt-x", provider="openai", api_key="k",
                 temperature=temperature, seed=seed)


def _panel(temperature=None, seed=None):
    cfg = JudgeConfig(role="technical_expert", model="gpt-x", provider="openai",
                      temperature=temperature, seed=seed)
    return JudgePanel(judges=[cfg]), cfg


def _grade_judge(judge, **kw):
    return judge.score_answer(library_name="L", question="q", answer="a", **kw)


def _grade_panel(panel, cfg):
    return panel._call_judge(cfg, "q", "a", "L", "")


def _usage(judge) -> dict:
    return dict(judge._usage_totals)


def _files(cache_dir) -> list:
    return sorted(p.name for p in cache_dir.glob("*.json"))


# ── Judge ────────────────────────────────────────────────────────────────────


def test_judge_parse_ok_makes_one_draw_zero_call(provider_calls, sent):
    provider_calls.push(REPLY)
    judge = _judge()
    out = _grade_judge(judge)
    assert sent == [(JUDGE_PROMPT, 0, False, (True, False), None, None)]
    assert provider_calls.by_purpose() == {"judge": 1}
    assert out == {
        "reasoning": "r", "correctness": 90.0, "completeness": 70.0, "specificity": 60.0,
        "code_quality": 80.0, "actionability": 50.0, "factual_grounding": 100,
        "aggregate": 76.0,
    }
    assert _usage(judge) == {
        "calls": 1, "cost_known_calls": 0, "cost_usd": 0.0, "prompt_tokens": 10,
        "completion_tokens": 5, "total_tokens": 15, "redrawn_judgements": 0,
    }


def test_judge_parse_fail_then_ok_redraws_with_no_reminder_when_unpinned(provider_calls, sent):
    provider_calls.push(BAD, REPLY)
    judge = _judge()
    out = _grade_judge(judge)
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), None, None),
        (JUDGE_PROMPT, 1, False, (True, False), None, None),
    ]
    assert out["aggregate"] == 76.0
    assert _usage(judge)["calls"] == 2
    assert _usage(judge)["redrawn_judgements"] == 1


def test_judge_parse_fail_then_ok_adds_the_reminder_when_pinned(provider_calls, sent):
    provider_calls.push(BAD, REPLY)
    judge = _judge(**PINNED)
    out = _grade_judge(judge)
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), 0.0, 7),
        (JUDGE_REMINDER_1, 1, True, (True, False), 0.0, 7),
    ]
    assert out["aggregate"] == 76.0
    assert _usage(judge)["redrawn_judgements"] == 1


@pytest.mark.parametrize("pin", [{"temperature": 0.0}, {"seed": 7}],
                         ids=["temperature_only", "seed_only"])
def test_judge_pinned_by_either_knob_adds_the_reminder(provider_calls, sent, pin):
    # The pinned rule is "temperature or seed is set"; each knob alone must pin.
    provider_calls.push(BAD, REPLY)
    judge = _judge(**pin)
    out = _grade_judge(judge)
    sampling = (pin.get("temperature"), pin.get("seed"))
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), *sampling),
        (JUDGE_REMINDER_1, 1, True, (True, False), *sampling),
    ]
    assert out["aggregate"] == 76.0


def test_judge_all_three_unparseable_raises_the_last_parse_error(provider_calls, sent):
    provider_calls.push(BAD, "still not", "nope")
    judge = _judge(**PINNED)
    with pytest.raises(ValueError) as exc:
        _grade_judge(judge)
    assert str(exc.value) == NO_JSON
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), 0.0, 7),
        (JUDGE_REMINDER_1, 1, True, (True, False), 0.0, 7),
        (JUDGE_REMINDER_2, 2, True, (True, False), 0.0, 7),
    ]
    assert _usage(judge)["calls"] == 3
    assert _usage(judge)["redrawn_judgements"] == 1


def test_judge_draw_argument_multiplies_into_the_attempt_draws(provider_calls, sent):
    provider_calls.push(BAD, REPLY)
    _grade_judge(_judge(), draw=2)
    assert [d for _, d, *_ in sent] == [6, 7]


def test_judge_ground_truth_path_uses_the_grounded_prompt(provider_calls, sent):
    provider_calls.push(REPLY)
    judge = _judge()
    out = judge.score_answer(library_name="L", question="q", answer="a",
                             ground_truth="the documented fact")
    assert sent == [(GROUNDED_PROMPT, 0, False, (True, False), None, None)]
    assert out["grounded"] is True
    assert out["aggregate"] == 82.9


def test_judge_transport_error_propagates_at_once_and_is_not_redrawn(provider_calls, sent):
    provider_calls.push(RuntimeError("boom"))
    judge = _judge()
    with pytest.raises(RuntimeError, match="boom"):
        _grade_judge(judge)
    assert [d for _, d, *_ in sent] == [0]
    assert _usage(judge)["calls"] == 0


def test_judge_rerun_with_the_cache_on_replays_the_stored_redraw(provider_calls, sent, cache_dir):
    provider_calls.push(BAD, REPLY)
    judge = _judge(**PINNED)
    _grade_judge(judge)
    assert _files(cache_dir) == ["f0cd923dc4fd72e7e4746e77505f11ad80878e92ee2190c52df6c64fe78b531f.json"]
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), 0.0, 7),
        (JUDGE_REMINDER_1, 1, True, (True, False), 0.0, 7),
        (JUDGE_REMINDER_2, 2, True, (True, False), 0.0, 7),
        (JUDGE_PROMPT, 0, False, (True, False), 0.0, 7),
        (JUDGE_REMINDER_1, 1, True, (True, False), 0.0, 7),
    ]
    assert len(provider_calls.calls) == 2

    sent.clear()
    provider_calls.calls.clear()
    out = _grade_judge(judge)
    assert out["aggregate"] == 76.0
    assert provider_calls.calls == []  # replayed, nothing sent
    assert sent == [
        (JUDGE_PROMPT, 0, False, (True, False), 0.0, 7),
        (JUDGE_REMINDER_1, 1, True, (True, False), 0.0, 7),
    ]
    assert _usage(judge)["calls"] == 3
    assert _usage(judge)["redrawn_judgements"] == 2


def test_judge_rerun_at_a_later_draw_replays_its_own_stored_reply(provider_calls, sent, cache_dir):
    # draw=1 owns the attempt draws 3..5 (draw * 3 + i). Its rerun must probe those,
    # not draws 0..2, or the stored reply is missed and asked live again.
    provider_calls.push(REPLY)
    judge = _judge()
    assert _grade_judge(judge, draw=1)["aggregate"] == 76.0
    assert len(provider_calls.calls) == 1

    sent.clear()
    provider_calls.calls.clear()
    out = _grade_judge(judge, draw=1)
    assert out["aggregate"] == 76.0
    assert provider_calls.calls == []  # replayed, nothing sent
    assert sent == [(JUDGE_PROMPT, 3, False, (True, False), None, None)]


def test_judge_all_fail_stores_nothing_in_the_cache(provider_calls, sent, cache_dir):
    provider_calls.push(BAD, BAD, BAD)
    with pytest.raises(ValueError):
        _grade_judge(_judge(**PINNED))
    assert _files(cache_dir) == []
    assert len(provider_calls.calls) == 3


# ── Panel ────────────────────────────────────────────────────────────────────


def test_panel_parse_ok_makes_one_draw_zero_call_and_keeps_the_vote(provider_calls, sent):
    provider_calls.push(REPLY)
    panel, cfg = _panel()
    vote = _grade_panel(panel, cfg)
    assert sent == [(PANEL_PROMPT, 0, False, (True, False), None, None)]
    assert provider_calls.by_purpose() == {"panel:technical_expert": 1}
    assert vote.error is None
    assert vote.aggregate == 72.3
    assert vote.scores == {
        "correctness": 90.0, "completeness": 70.0, "specificity": 60.0,
        "code_quality": 80.0, "actionability": 50.0,
    }


def test_panel_parse_fail_then_ok_redraws_with_no_reminder_when_unpinned(provider_calls, sent):
    provider_calls.push(BAD, REPLY)
    panel, cfg = _panel()
    vote = _grade_panel(panel, cfg)
    assert sent == [
        (PANEL_PROMPT, 0, False, (True, False), None, None),
        (PANEL_PROMPT, 1, False, (True, False), None, None),
    ]
    assert vote.aggregate == 72.3


def test_panel_parse_fail_then_ok_adds_the_reminder_when_pinned(provider_calls, sent):
    provider_calls.push(BAD, REPLY)
    panel, cfg = _panel(**PINNED)
    vote = _grade_panel(panel, cfg)
    assert sent == [
        (PANEL_PROMPT, 0, False, (True, False), 0.0, 7),
        (PANEL_REMINDER_1, 1, True, (True, False), 0.0, 7),
    ]
    assert vote.aggregate == 72.3


@pytest.mark.parametrize("pin", [{"temperature": 0.0}, {"seed": 7}],
                         ids=["temperature_only", "seed_only"])
def test_panel_pinned_by_either_knob_adds_the_reminder(provider_calls, sent, pin):
    # The pinned rule is "temperature or seed is set"; each knob alone must pin.
    provider_calls.push(BAD, REPLY)
    panel, cfg = _panel(**pin)
    vote = _grade_panel(panel, cfg)
    sampling = (pin.get("temperature"), pin.get("seed"))
    assert sent == [
        (PANEL_PROMPT, 0, False, (True, False), *sampling),
        (PANEL_REMINDER_1, 1, True, (True, False), *sampling),
    ]
    assert vote.aggregate == 72.3


def test_panel_all_three_unparseable_records_the_error_on_the_vote(provider_calls, sent):
    provider_calls.push(BAD, "still not", "nope")
    panel, cfg = _panel(**PINNED)
    vote = _grade_panel(panel, cfg)
    assert sent == [
        (PANEL_PROMPT, 0, False, (True, False), 0.0, 7),
        (PANEL_REMINDER_1, 1, True, (True, False), 0.0, 7),
        (PANEL_REMINDER_2, 2, True, (True, False), 0.0, 7),
    ]
    assert vote.aggregate is None
    assert vote.scores == {}
    assert vote.error == NO_JSON


def test_panel_transport_error_becomes_the_vote_error_with_no_redraw(provider_calls, sent):
    provider_calls.push(RuntimeError("boom"))
    panel, cfg = _panel()
    vote = _grade_panel(panel, cfg)
    assert [d for _, d, *_ in sent] == [0]
    assert vote.aggregate is None
    assert vote.error == "boom"


def test_panel_rerun_with_the_cache_on_replays_the_stored_redraw(provider_calls, sent, cache_dir):
    provider_calls.push(BAD, REPLY)
    panel, cfg = _panel(**PINNED)
    _grade_panel(panel, cfg)
    assert _files(cache_dir) == ["50554ebc560c85cc138281ff31f5245b8208cc9f260ced6a82c763814cb7c883.json"]
    assert len(sent) == 5  # three replay probes, then the two live draws
    assert len(provider_calls.calls) == 2

    sent.clear()
    provider_calls.calls.clear()
    vote = _grade_panel(panel, cfg)
    assert vote.aggregate == 72.3
    assert provider_calls.calls == []
    assert sent == [
        (PANEL_PROMPT, 0, False, (True, False), 0.0, 7),
        (PANEL_REMINDER_1, 1, True, (True, False), 0.0, 7),
    ]


def test_panel_all_fail_stores_nothing_in_the_cache(provider_calls, sent, cache_dir):
    provider_calls.push(BAD, BAD, BAD)
    panel, cfg = _panel(**PINNED)
    _grade_panel(panel, cfg)
    assert _files(cache_dir) == []
    assert len(provider_calls.calls) == 3
