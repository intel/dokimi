# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A judge reply is used and cached only if every dimension is a finite number.

NaN, Infinity and booleans used to be accepted, scored and stored, so one bad
reply replayed on every later run. A numeric string is read as a number, as
panel.py does. Stubs only, no network.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from dokimi import caching
from dokimi.eval.judge import Judge
from dokimi.utils import get_llm

DIMS = ("correctness", "completeness", "specificity", "code_quality", "actionability")


def _reply(**over):
    scores = {d: 80 for d in DIMS}
    scores.update(over)
    return json.dumps({**scores, "aggregate": 0}, allow_nan=True)


GOOD = _reply()


def _stub(monkeypatch, texts):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=texts[len(calls) - 1]))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            model="m",
        )

    monkeypatch.setattr("litellm.completion", completion)
    return calls


def _judge():
    judge = Judge.__new__(Judge)
    judge._accumulate_usage = lambda usage: None
    judge.temperature = judge.seed = None
    judge._usage_lock, judge._usage_totals = threading.Lock(), {"redrawn_judgements": 0}
    judge.llm = get_llm("amazon-bedrock", "m.x")
    return judge


def _score(judge, ground_truth=None):
    return judge._judge_answer(
        library_name="L", question="q", answer="a", context="c", ground_truth=ground_truth
    )


def _entries(cache_dir):
    return [p for p in cache_dir.rglob("*") if p.is_file()]


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


REJECTED = [
    pytest.param(_reply(correctness="NaN").replace('"NaN"', "NaN"), id="nan"),
    pytest.param(_reply(correctness="X").replace('"X"', "Infinity"), id="infinity"),
    pytest.param(_reply(correctness="X").replace('"X"', "-Infinity"), id="minus-infinity"),
    pytest.param(_reply(correctness="X").replace('"X"', "1e999"), id="1e999"),
    pytest.param(_reply(correctness="X").replace('"X"', "1" + "0" * 400), id="int-past-float-range"),
    pytest.param(_reply(specificity="X").replace('"X"', "NaN"), id="nan-in-unweighted-specificity"),
    pytest.param(_reply(correctness=True), id="true"),
    pytest.param(_reply(completeness=False), id="false"),
    pytest.param(_reply(correctness="NaN"), id="nan-string"),
    pytest.param(_reply(code_quality="N/A"), id="n-a"),
    pytest.param(_reply(code_quality=None), id="null"),
    pytest.param('{"correctness": 80}', id="missing-dimension"),
    pytest.param("[80, 80, 80]", id="array"),
    pytest.param("{}", id="empty-object"),
]


@pytest.mark.parametrize("bad", REJECTED)
def test_bad_reply_is_retried_then_an_error_and_never_cached(cache_dir, monkeypatch, bad):
    calls = _stub(monkeypatch, [bad, bad, bad])
    with pytest.raises((ValueError, AttributeError)):  # a JSON array has no .keys()
        _score(_judge())
    assert len(calls) == 3
    assert _entries(cache_dir) == []


@pytest.mark.parametrize("bad", REJECTED)
def test_bad_first_reply_then_valid_one(cache_dir, monkeypatch, bad):
    calls = _stub(monkeypatch, [bad, GOOD])
    assert _score(_judge())["aggregate"] == 80.0
    assert len(calls) == 2
    assert len(_entries(cache_dir)) == 1


def test_valid_reply_is_unchanged_and_cached(cache_dir, monkeypatch):
    calls = _stub(monkeypatch, [_reply(correctness=90, completeness=70.5)])
    scores = _score(_judge())
    assert (scores["correctness"], scores["completeness"]) == (90, 70.5)
    assert all(type(scores[d]) is float for d in DIMS)
    assert scores["aggregate"] == round((90 * 2 + 70.5 + 80 + 80) / 5, 1)
    assert len(calls) == 1 and len(_entries(cache_dir)) == 1


def test_numeric_string_score_is_read_as_a_number(cache_dir, monkeypatch):
    calls = _stub(monkeypatch, [_reply(correctness="90")])
    scores = _score(_judge())
    assert scores["correctness"] == 90.0
    assert len(calls) == 1 and len(_entries(cache_dir)) == 1


@pytest.mark.parametrize("given, kept", [(150, 100), (-20, 0)])
def test_out_of_range_score_is_clamped_to_the_scale(cache_dir, monkeypatch, given, kept):
    _stub(monkeypatch, [_reply(completeness=given, specificity=given)])
    scores = _score(_judge())
    assert scores["completeness"] == scores["specificity"] == kept
    assert 0 <= scores["aggregate"] <= 100


@pytest.mark.parametrize("bad", [
    pytest.param(_reply(factual_grounding="X").replace('"X"', "NaN"), id="nan"),
    pytest.param(_reply(factual_grounding=True), id="true"),
    pytest.param(_reply(), id="missing"),
])
def test_grounded_reply_checks_factual_grounding_too(cache_dir, monkeypatch, bad):
    calls = _stub(monkeypatch, [bad, _reply(factual_grounding=150)])
    scores = _score(_judge(), ground_truth="excerpt")
    assert scores["grounded"] is True
    assert scores["factual_grounding"] == 100.0  # clamped, and weighted 2 like correctness
    assert scores["aggregate"] == round((80 * 2 + 80 + 80 + 80 + 100 * 2) / 7, 1)
    assert len(calls) == 2 and len(_entries(cache_dir)) == 1


def test_nan_correctness_no_longer_bypasses_the_wrong_figure_cap(cache_dir, monkeypatch):
    bad = _reply(correctness="X", numeric_claims=["64 B: wrong"]).replace('"X"', "NaN")
    _stub(monkeypatch, [bad, _reply(correctness=95, numeric_claims=["64 B: wrong"])])
    scores = _score(_judge())
    assert scores["correctness"] == 40.0


def test_poisoned_entry_from_the_old_code_is_not_replayed(cache_dir, monkeypatch):
    nan = _reply(correctness="X").replace('"X"', "NaN")
    with monkeypatch.context() as old:  # the old judge stored whatever reply it was given
        old.setattr(caching, "keeps", lambda cache_if, text: True)
        _stub(old, [nan, nan, nan])
        with pytest.raises(ValueError):
            _score(_judge())
        assert len(_entries(cache_dir)) == 3
    calls = _stub(monkeypatch, [GOOD])
    assert _score(_judge())["aggregate"] == 80.0
    assert len(calls) == 1  # the stored NaN was refused on replay and a fresh draw was asked
