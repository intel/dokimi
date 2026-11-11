# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Replay provenance per answer in arms runs (issue #370): the runner records which answers
the response cache served, and ``eval headroom`` does not count a cached repeat as a fresh rep."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dokimi import caching, records
from dokimi.artifacts import stamp, validate_artifact
from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.headroom import format_table, headroom
from dokimi.treatments import BaselineTreatment
from tests.headroom_fixtures import _run

QUESTIONS = [{"id": "q1", "question": "How do I parallelize a loop?"},
             {"id": "q2", "question": "How do I sum an array in parallel?"}]


@pytest.fixture
def completions(tmp_path, monkeypatch):
    """The response cache on in ``tmp_path``; litellm answers each live call with a new text."""
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "cache"))
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"answer {len(calls)}"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12), model="m")

    monkeypatch.setattr("litellm.completion", completion)
    return calls


def _replayed(recs):
    return {r["question_id"]: r["arms"]["baseline"].get("replayed") for r in recs}


def test_a_cache_hit_is_recorded_as_a_replayed_answer(completions):
    runner = ArmRunner([BaselineTreatment()], model="gpt-4o-mini", provider="openai")
    first = runner.run("lib", QUESTIONS, concurrency=1)
    second = runner.run("lib", QUESTIONS, concurrency=1)
    assert _replayed(first) == {"q1": False, "q2": False}
    assert _replayed(second) == {"q1": True, "q2": True}
    assert len(completions) == 2  # the second run made no call
    assert [r["arms"]["baseline"]["answer"] for r in second] == [r["arms"]["baseline"]["answer"] for r in first]


def test_only_the_answer_the_cache_served_is_marked(completions):
    runner = ArmRunner([BaselineTreatment()], model="gpt-4o-mini", provider="openai")
    runner.run("lib", QUESTIONS[:1], concurrency=1)
    assert _replayed(runner.run("lib", QUESTIONS, concurrency=1)) == {"q1": True, "q2": False}


def test_an_arms_artifact_with_the_flag_is_saved(completions, tmp_path):
    runner = ArmRunner([BaselineTreatment()], model="gpt-4o-mini", provider="openai")
    runner.run("lib", QUESTIONS, concurrency=1)
    path = tmp_path / "arms.json"
    runner.save(runner.build_output("lib", runner.run("lib", QUESTIONS, concurrency=1)), path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [r["arms"]["baseline"]["replayed"] for r in saved["answers"]] == [True, True]


def test_records_read_the_flag_and_a_file_without_it_reads_as_not_replayed():
    data = {"baseline_arm": "baseline", "answers": [
        {"question_id": "a", "arms": {"baseline": {"answer": "x", "replayed": True}}},
        {"question_id": "b", "arms": {"baseline": {"answer": "x"}}},
        {"question_id": "c", "arms": {"baseline": {"answer": "x", "replayed": "true"}}},
    ]}
    run = records.read(data, source="test")
    assert [r.answer("baseline").replayed for r in run] == [True, False, False]


def _arms_run(items, *, replayed=False, text="x", baseline="baseline"):
    """An arms.v1 payload whose answers all carry ``text`` and the given ``replayed`` flag."""
    run = _run(items, baseline)
    raw = {"baseline_arm": baseline, "answers": [], "evaluations": []}
    for rec in run:
        raw["answers"].append({"question_id": rec.question_id, "question_text": rec.question_text,
                               "arms": {arm: {"answer": text, "replayed": replayed} for arm in rec.conditions}})
        raw["evaluations"].append({"question_id": rec.question_id, "scores": dict(rec.raw["scores"])})
    return records.read(raw, source="test")


SCORES = [("q1", {"baseline": 95, "skill": 96})]


def test_a_fully_replayed_repeat_adds_no_rep():
    result = headroom([_arms_run(SCORES), _arms_run(SCORES, replayed=True)], min_reps=2)
    item = result["items"][0]
    assert (item["reps"], item["provisional"]) == (1, True)
    assert result["summary"]["replayed_repeats"] == 2  # both arms of the one item
    assert ("note: 2 replayed answers (DOKIMI_RESPONSE_CACHE) left out of the reps and the means: "
            "another run gave the same answer") in format_table(result).splitlines()


def test_a_replayed_repeat_does_not_move_the_mean():
    fresh = _arms_run([("q1", {"baseline": 80, "skill": 80})])
    replay = _arms_run([("q1", {"baseline": 100, "skill": 100})], replayed=True)  # re-judged higher
    item = headroom([replay, fresh])["items"][0]  # the replay's file comes first
    assert item["no_skill"] == 0.8 and item["flag"] is None


def test_a_replay_whose_original_is_not_an_input_counts_once():
    replays = [_arms_run(SCORES, replayed=True), _arms_run(SCORES, replayed=True)]
    result = headroom(replays, min_reps=1)
    assert result["items"][0]["reps"] == 1
    assert "note: 2 replayed answers" in format_table(result)
    assert result["summary"]["replayed_repeats"] == 2


def test_replays_with_no_fresh_original_count_at_their_mean_in_any_order():
    first = _arms_run([("q1", {"baseline": 80, "skill": 80})], replayed=True)
    second = _arms_run([("q1", {"baseline": 100, "skill": 100})], replayed=True)  # re-judged higher
    for runs in ([first, second], [second, first]):
        result = headroom(runs, min_reps=1)
        item = result["items"][0]
        assert (item["reps"], item["no_skill"], item["flag"]) == (1, 0.9, "ceiling")
        assert result["summary"]["replayed_repeats"] == 2


def test_a_replayed_answer_with_another_text_is_a_sample_of_its_own():
    result = headroom([_arms_run(SCORES, text="a"), _arms_run(SCORES, text="b", replayed=True)])
    assert result["items"][0]["reps"] == 2
    assert result["summary"]["replayed_repeats"] == 0


def test_a_replay_of_an_unscored_answer_brings_the_score():
    unjudged = _arms_run([("q1", {"baseline": None, "skill": None})])
    result = headroom([unjudged, _arms_run(SCORES, replayed=True)], min_reps=1)
    assert result["items"][0]["reps"] == 1 and result["items"][0]["flag"] == "ceiling"
    assert result["summary"]["replayed_repeats"] == 0


def test_runs_without_the_flag_count_as_before():
    result = headroom([_run(SCORES), _run(SCORES)], min_reps=2)
    assert result["items"][0]["reps"] == 2 and not result["items"][0]["provisional"]
    assert result["summary"]["replayed_repeats"] == 0
    assert "replayed" not in format_table(result)


def test_the_headroom_artifact_carries_the_count():
    result = headroom([_arms_run(SCORES), _arms_run(SCORES, replayed=True)])
    result["inputs"] = ["a.json", "b.json"]
    saved = json.loads(json.dumps(stamp("headroom", result)))
    validate_artifact("headroom", saved)
    assert saved["summary"]["replayed_repeats"] == 2
