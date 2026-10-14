# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Property, boundary and CLI tests for the judge panel (stubs only, no network)."""

from __future__ import annotations

import json
import random
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dokimi import caching
from dokimi.eval.panel import ROLE_WEIGHTS, SCORE_FIELDS, JudgeConfig, JudgePanel

ROLES = list(ROLE_WEIGHTS)


def _reply(**kw):
    d = {k: 70 for k in SCORE_FIELDS}
    d.update(kw)
    return json.dumps(d)


def _panel(roles=None, **cfg):
    return JudgePanel(judges=[JudgeConfig(role=r, model="m", provider="openai", **cfg)
                              for r in (roles or ROLES)])


def _litellm(texts):
    calls = []

    def completion(**kw):
        calls.append(kw)
        item = texts[min(len(calls), len(texts)) - 1]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2), model="m")

    return completion, calls


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "rc"))


def _vote(panel, raw):
    with patch("dokimi.eval.panel.llm_call", return_value=raw) as m:
        return panel._call_judge(panel.judges[0], "q", "a", "lib", "ctx"), m


def _vote_real(panel):
    return panel._call_judge(panel.judges[0], "q", "a", "lib", "c")


def _record(doc_key, q_key):
    return {"question_id": "q1", q_key: "QTEXT",
            "with_docs": {"answer": "A", "retrieved_docs": [{doc_key: "DOCTEXT", "source": "s"}]},
            "without_docs": {"answer": "B"}}


def test_fallback_keys_give_same_prompts_as_answers_v1_keys():
    """Prompts and results match for answers.v1 keys and for question/content keys."""
    seen = {}
    for name, (dk, qk) in {"v1": ("snippet", "question_text"), "alt": ("content", "question"), "alt2": ("content", "text")}.items():
        prompts = []
        with patch("dokimi.eval.panel.llm_call",
                   side_effect=lambda p, _sink=prompts, **k: _sink.append(p) or _reply()):
            res = _panel().evaluate_answers([_record(dk, qk)], "lib")
        for r in res:
            for arm in ("with_docs", "without_docs"):
                r[arm] and r[arm].pop("votes")
        seen[name] = (sorted(prompts), res)
        assert all("QTEXT" in p for p in prompts) and any("DOCTEXT" in p for p in prompts)
    assert seen["v1"] == seen["alt"] == seen["alt2"]


def test_role_order_does_not_change_aggregate():
    """Shuffling the judge list leaves the mean, std and dimensions unchanged."""
    rng = random.Random(7)
    by_role = {r: _reply(**{k: rng.randint(0, 100) for k in SCORE_FIELDS}) for r in ROLES}

    def run(roles):
        panel = JudgePanel(judges=[JudgeConfig(role=r, model=r, provider="openai") for r in roles])
        with patch("dokimi.eval.panel.llm_call", side_effect=lambda p, **k: by_role[k["model"]]):
            return panel.evaluate("q", "a", "lib", "c")

    base = run(ROLES)
    for _ in range(5):
        v = run(rng.sample(ROLES, len(ROLES)))
        assert (v.mean_aggregate, v.std_aggregate, v.mean_dimensions) == \
               (base.mean_aggregate, base.std_aggregate, base.mean_dimensions)


def test_role_failing_all_retries_is_excluded_not_averaged_in():
    """A role with only unparseable replies leaves the mean of the others untouched."""
    judges = [JudgeConfig(role=r, model="ok", provider="openai") for r in ROLES[:2]]
    judges.append(JudgeConfig(role=ROLES[2], model="bad", provider="openai"))
    with patch("dokimi.eval.panel.llm_call",
               side_effect=lambda p, **k: "garbage" if k["model"] == "bad" else _reply()) as m:
        v = JudgePanel(judges=judges).evaluate("q", "a", "lib", "c")
    assert v.mean_aggregate == 70.0
    assert [x.error is not None for x in v.votes if x.model == "bad"] == [True]
    assert sum(1 for c in m.call_args_list if c.kwargs["model"] == "bad") == 3


@pytest.mark.parametrize("cfg,reminder", [({}, False), ({"temperature": 0.0}, True), ({"seed": 3}, True)])
def test_retry_draws_are_distinct_and_reminder_only_when_pinned(cfg, reminder):
    """Attempt N uses draw N; the format reminder is appended only with sampling pinned."""
    calls = []

    def fake(prompt, **k):
        calls.append((prompt, k["draw"]))
        return "nope" if len(calls) < 3 else _reply()

    panel = _panel(ROLES[:1], **cfg)
    with patch("dokimi.eval.panel.llm_call", side_effect=fake):
        v = _vote_real(panel)
    assert v.error is None and [d for _, d in calls] == [0, 1, 2]
    assert [("Reminder" in p) for p, _ in calls] == [False, reminder, reminder]
    assert len({p for p, _ in calls}) == (3 if reminder else 1)


def test_unparseable_replies_are_not_replayed_from_the_cache(cache_dir, monkeypatch):
    """A run that exhausted its attempts does not poison the next run's cache."""
    comp, calls = _litellm(["junk", "junk", "junk", _reply()])
    monkeypatch.setattr("litellm.completion", comp)
    assert _vote_real(_panel(ROLES[:1])).error is not None
    assert _vote_real(_panel(ROLES[:1])).error is None
    assert len(calls) == 4


@pytest.mark.parametrize("raw", ['{"correctness": "N/A"}', '{"correctness": null}'])
def test_non_numeric_dimension_is_retried(raw):
    """A non-numeric dimension triggers all three attempts."""
    v, m = _vote(_panel(ROLES[:1]), raw)
    assert m.call_count == 3 and v.error





def test_cli_panel_score_end_to_end(tmp_path, monkeypatch, capsys, cache_dir):
    """`dokimi eval panel-score` reads answers.v1 data and writes the panel file."""
    from dokimi.cli import main
    answers = tmp_path / "a.json"
    answers.write_text(json.dumps({"answers": [_record("snippet", "question_text")]}))
    out = tmp_path / "out.json"
    comp, calls = _litellm([_reply()])
    monkeypatch.setattr("litellm.completion", comp)
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "panel-score", "--answers", str(answers),
                                      "--product", "lib", "--output", str(out)])
    main()
    data = json.loads(out.read_text())
    assert "Panel score (context arm): 70.0/100" in capsys.readouterr().out
    texts = [c["messages"][-1]["content"] for c in calls]
    assert all("QTEXT" in t for t in texts) and any("DOCTEXT" in t for t in texts)
    assert data["evaluations"][0]["with_docs"]["aggregate"] == 70.0
