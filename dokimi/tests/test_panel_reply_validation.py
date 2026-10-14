# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A panel reply is a vote only if it carries every finite numeric dimension."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dokimi import caching
from dokimi.eval.panel import SCORE_FIELDS, JudgeConfig, JudgePanel

GOOD = json.dumps({"reasoning": "ok", **{k: 70 for k in SCORE_FIELDS}})
ALL_NAN = "{" + ", ".join(f'"{k}": NaN' for k in SCORE_FIELDS) + "}"
BAD = [
    "{}",
    '{"reasoning": "no scores"}',
    "[1, 2]",
    '{"reasoning": "x", "correctness": 90}',
    '{"correctness": NaN, "completeness": 50, "specificity": 50, '
    '"code_quality": 50, "actionability": 50}',
    '{"correctness": Infinity, "completeness": 50, "specificity": 50, '
    '"code_quality": 50, "actionability": 50}',
    ALL_NAN,
    json.dumps({k: True for k in SCORE_FIELDS}),
]


def _judge():
    cfg = JudgeConfig(role="technical_expert", model="m", provider="openai")
    return JudgePanel(judges=[cfg]), cfg


@pytest.mark.parametrize("raw", BAD)
def test_unusable_reply_is_retried_and_not_a_vote(raw):
    panel, cfg = _judge()
    with patch("dokimi.eval.panel.llm_call", return_value=raw) as m:
        vote = panel._call_judge(cfg, "q", "a", "lib", "c")
    assert m.call_count == 3
    assert vote.error and vote.scores == {} and vote.aggregate is None


@pytest.mark.parametrize("raw", BAD)
def test_unusable_reply_is_not_stored_in_the_response_cache(raw, tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "rc"))
    replies = [raw, raw, raw, GOOD]
    calls = []

    def completion(**kw):
        calls.append(kw)
        text = replies[min(len(calls), len(replies)) - 1]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="m")

    monkeypatch.setattr("litellm.completion", completion)
    panel, cfg = _judge()
    assert panel._call_judge(cfg, "q", "a", "lib", "c").error
    assert panel._call_judge(cfg, "q", "a", "lib", "c").aggregate == 70.0
    assert len(calls) == 4


def test_full_reply_recovers_after_a_truncated_one():
    panel, cfg = _judge()
    with patch("dokimi.eval.panel.llm_call", side_effect=[BAD[3], GOOD]) as m:
        vote = panel._call_judge(cfg, "q", "a", "lib", "c")
    assert m.call_count == 2 and vote.error is None and vote.aggregate == 70.0
