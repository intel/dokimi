# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The zero-score check counts unjudged retrieval misses as unusable.

A question whose retrieval missed gets no with-docs judgement, so it has no
context score. The zero-score check must still count it. Otherwise a run where
most questions miss passes a fail-severity check it fails on intel/main.
"""

from unittest.mock import MagicMock

from dokimi.eval.diagnoser import EMPTY_RETRIEVAL, INSUFFICIENT_DATA, summarise_diagnoses
from dokimi.eval.judge import Judge
from dokimi.gate.trust_gate import evaluate_trust

GOOD = MagicMock(content=(
    '{"correctness": 60, "completeness": 60, "specificity": 60, '
    '"code_quality": 60, "actionability": 60, "aggregate": 0}'
))
ZERO = MagicMock(content=(
    '{"correctness": 0, "completeness": 0, "specificity": 0, '
    '"code_quality": 0, "actionability": 0, "aggregate": 0}'
))
HIT = {
    "answer": "use parallel_for",
    "doc_source": "context7",
    "retrieved_docs": [{"source": "context7", "snippet": "parallel_for ...",
                        "relevance_score": 0.8}],
}
MISS = {
    "answer": "[FALLBACK: No relevant documentation found]",
    "doc_source": "fallback_none",
    "retrieved_docs": [],
}


def _rows(n_hit, n_miss):
    """Judge n_hit questions with a document and n_miss without, as the judge does."""
    judge = Judge.__new__(Judge)
    judge._accumulate_usage = lambda usage: None
    judge.temperature = judge.seed = None
    judge.llm = MagicMock()
    judge.llm.invoke.side_effect = lambda msgs, *a, **k: ZERO if "FALLBACK" in str(msgs) else GOOD
    rows = []
    for i in range(n_hit + n_miss):
        rows.append(judge._evaluate_answer_pair("L", {
            "question_id": f"q{i}",
            "question_text": "q?",
            "with_docs": dict(HIT if i < n_hit else MISS),
            "without_docs": {"answer": "baseline"},
        }))
    return rows


def _zero_check(verdict):
    (check,) = [c for c in verdict.checks if c.name == "zero_score_fraction"]
    return check


def test_four_misses_of_fourteen_fail_the_zero_check():
    check = _zero_check(evaluate_trust(_rows(10, 4)))
    assert check.severity == "fail"
    assert check.value == 0.286
    assert check.passed is False


def test_all_hits_pass_the_zero_check():
    check = _zero_check(evaluate_trust(_rows(14, 0)))
    assert check.value == 0.0
    assert check.passed is True


def _retrieval_check(verdict):
    (check,) = [c for c in verdict.checks if c.name == "retrieval_hit_rate"]
    return check


def test_judge_error_rows_keep_the_retrieval_outcome_of_their_answer(monkeypatch):
    """5 of 20 answers fail in the judge: the retrieval rate is still measured over all 20."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    judge = Judge(model="gpt-x", provider="openai")
    judge.llm = MagicMock()
    judge.llm.invoke.side_effect = lambda msgs, *a, **k: GOOD
    real = judge._evaluate_answer_pair

    def flaky(library_name, item):
        if int(item.question_id[1:]) >= 15:
            raise RuntimeError("judge down")
        return real(library_name, item)

    judge._evaluate_answer_pair = flaky
    answers = [
        {"question_id": f"q{i}", "question_text": "q?",
         "with_docs": dict(HIT), "without_docs": {"answer": "baseline"}}
        for i in range(20)
    ]
    rows = judge.evaluate_answers("L", answers)
    assert sum("error" in r for r in rows) == 5
    check = _retrieval_check(evaluate_trust(rows))
    assert check.value == 1.0
    assert check.measured is not False
    assert "(20/20)" in check.message


def test_unjudged_misses_stay_empty_retrievals_in_the_failure_analysis():
    summary = summarise_diagnoses(_rows(10, 4))
    assert summary["counts"][EMPTY_RETRIEVAL] == 4
    assert summary["counts"][INSUFFICIENT_DATA] == 0
