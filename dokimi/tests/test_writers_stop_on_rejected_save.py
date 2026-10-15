# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A rejected incremental save stops the run and keeps the paid results as they were.

The save used to sit inside the per-question ``try``: the validation error turned the paid,
good result into an error record, the handler saved again, and the pool still ran (and paid
for) every queued question before the error surfaced.
"""

from __future__ import annotations

import json
import time
from unittest.mock import Mock, patch

import pytest

from dokimi.artifacts import ArtifactValidationError

# question_id 1.5 is neither a string nor an integer: answers.v1 and eval.v1 reject it


def _ids():
    return ["q0", 1.5, "q2", "q3", "q4", "q5"]


def _hold_q0_until_rejected(out, qid, concurrency):
    """With two workers, q0 waits until the bad question's save was rejected, so the run is
    deterministic: only the two questions already in flight can be paid for."""
    if concurrency > 1 and qid == "q0":
        rejected = out.with_name(out.name + ".rejected")
        deadline = time.time() + 5
        while not rejected.exists() and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)


def _answers_run(tmp_path, concurrency):
    from dokimi.eval.answerer import Answerer

    calls = []

    def pair(library_name, library_id, question, max_tokens):
        calls.append(question["id"])
        _hold_q0_until_rejected(out, question["id"], concurrency)
        return {"question_id": question["id"], "question_text": "t", "library_name": library_name,
                "with_docs": {"answer": "a"}, "without_docs": {"answer": "b"}}

    out = tmp_path / "answers.json"
    with patch("dokimi.eval.answerer.LANGCHAIN_AVAILABLE", True):
        answerer = Answerer(mcp_client=Mock())
        with patch.object(answerer, "_generate_answer_pair", side_effect=pair), \
             pytest.raises(ArtifactValidationError, match="question_id"):
            answerer.generate_answers("lib", "org/lib", [{"id": i, "text": "?"} for i in _ids()],
                                      concurrency=concurrency, output_path=out)
    return calls, out


def _judge_run(tmp_path, concurrency):
    from dokimi.eval.judge import Judge

    calls = []

    def evaluate(library_name, answer):
        calls.append(answer["question_id"])
        _hold_q0_until_rejected(out, answer["question_id"], concurrency)
        return {"question_id": answer["question_id"], "question_text": "t", "delta": 0}

    out = tmp_path / "eval.json"
    answers = [{"question_id": i, "question_text": "t"} for i in _ids()]
    with patch("dokimi.eval.judge.ChatAnthropic", Mock(), create=True), \
         patch("dokimi.eval.judge.LANGCHAIN_AVAILABLE", True):
        judge = Judge()
        with patch.object(judge, "_evaluate_answer_pair", side_effect=evaluate), \
             pytest.raises(ArtifactValidationError, match="question_id"):
            judge.evaluate_answers("lib", answers, concurrency=concurrency, output_path=out)
    return calls, out


@pytest.mark.parametrize("run, key", [(_answers_run, "answers"), (_judge_run, "evaluations")])
def test_a_rejected_save_stops_the_run_and_keeps_the_paid_results(run, key, tmp_path):
    calls, out = run(tmp_path, 1)
    assert calls == ["q0", 1.5]  # nothing is paid for after the rejected save
    kept = json.loads((tmp_path / (out.name + ".rejected")).read_text(encoding="utf-8"))[key]
    assert [r["question_id"] for r in kept] == ["q0", 1.5]
    assert not any(r.get("error") for r in kept)  # the paid result is not rewritten as an error
    assert [r["question_id"] for r in json.loads(out.read_text(encoding="utf-8"))[key]] == ["q0"]


@pytest.mark.parametrize("run", [_answers_run, _judge_run])
def test_a_rejected_save_under_concurrency_does_not_pay_for_the_queue(run, tmp_path):
    calls, _ = run(tmp_path, 2)
    assert sorted(map(str, calls)) == ["1.5", "q0"]
