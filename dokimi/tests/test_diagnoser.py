# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for failure diagnoser."""

import pytest
from dokimi import records
from dokimi.records import CONTEXT
from tests.artifact_fixtures import artifact_fixture
from dokimi.eval.diagnoser import (
    diagnose,
    summarise_diagnoses,
    DOCS_HELPED,
    EMPTY_RETRIEVAL,
    LOW_RELEVANCE,
    KNOWLEDGE_SUFFICIENT,
    INSUFFICIENT_DATA,
)


def _make_answer(doc_source="context7", retrieved_docs=None):
    """The context-arm answer of an answers.v1 record, as the judge hands it to diagnose()."""
    data = artifact_fixture("answers", {"answers": [{
        "question_id": "q1",
        "question_text": "How?",
        "with_docs": {"answer": "an answer", "doc_source": doc_source,
                      "retrieved_docs": retrieved_docs or []},
        "without_docs": {"answer": "some answer"},
    }]})
    return records.read(data).records[0].answer(CONTEXT)


# ── diagnose() ────────────────────────────────────────────────────────────────

def test_docs_helped_positive_delta():
    result = diagnose(_make_answer(), 5.0)
    assert result["label"] == DOCS_HELPED


def test_docs_helped_zero_delta():
    result = diagnose(_make_answer(), 0.0)
    assert result["label"] == DOCS_HELPED


def test_insufficient_data_no_delta():
    result = diagnose(_make_answer(), None)
    assert result["label"] == INSUFFICIENT_DATA


def test_empty_retrieval_no_docs():
    answer = _make_answer(doc_source="fallback_none", retrieved_docs=[])
    result = diagnose(answer, -3.0)
    assert result["label"] == EMPTY_RETRIEVAL


def test_unjudged_retrieval_miss_is_an_empty_retrieval_not_insufficient_data():
    """The judge leaves the no-document placeholder unjudged, so its delta is None."""
    answer = _make_answer(doc_source="fallback_none", retrieved_docs=[])
    result = diagnose(answer, None)
    assert result["label"] == EMPTY_RETRIEVAL
    assert result["evidence"]["doc_source"] == "fallback_none"


def test_empty_retrieval_no_mcp_client():
    answer = _make_answer(doc_source="none", retrieved_docs=[])
    result = diagnose(answer, -2.0)
    assert result["label"] == EMPTY_RETRIEVAL
    assert result["evidence"].get("reason") == "no_mcp_client"


def test_low_relevance():
    docs = [{"snippet": "a", "relevance_score": 0.1}, {"snippet": "b", "relevance_score": 0.05}]
    answer = _make_answer(doc_source="context7", retrieved_docs=docs)
    result = diagnose(answer, -4.0)
    assert result["label"] == LOW_RELEVANCE
    assert result["evidence"]["top_relevance_score"] == pytest.approx(0.1)


def test_knowledge_sufficient_high_relevance():
    docs = [{"snippet": "a", "relevance_score": 0.8}, {"snippet": "b", "relevance_score": 0.6}]
    answer = _make_answer(doc_source="context7", retrieved_docs=docs)
    result = diagnose(answer, -2.0)
    assert result["label"] == KNOWLEDGE_SUFFICIENT
    assert result["evidence"]["top_relevance_score"] == pytest.approx(0.8)


def test_knowledge_sufficient_no_score_field():
    """Docs without relevance_score → treated as knowledge_sufficient (not low_relevance)."""
    docs = [{"source": "docs", "snippet": "some content"}]  # no relevance_score
    answer = _make_answer(doc_source="context7", retrieved_docs=docs)
    result = diagnose(answer, -1.0)
    assert result["label"] == KNOWLEDGE_SUFFICIENT


def test_diagnosis_has_required_keys():
    answer = _make_answer(doc_source="fallback_none")
    result = diagnose(answer, -1.0)
    assert "label" in result
    assert "detail" in result
    assert "evidence" in result
    assert isinstance(result["detail"], str)
    assert len(result["detail"]) > 0


def test_custom_cutoff():
    """Custom cutoff: score=0.4 should be low_relevance when cutoff=0.5."""
    docs = [{"snippet": "a", "relevance_score": 0.4}]
    answer = _make_answer(doc_source="context7", retrieved_docs=docs)
    result = diagnose(answer, -1.0, low_relevance_cutoff=0.5)
    assert result["label"] == LOW_RELEVANCE


# ── summarise_diagnoses() ─────────────────────────────────────────────────────

def _eval_with_diagnosis(delta, label):
    diag = {"label": label, "detail": "", "evidence": {}}
    return {"delta": delta, "diagnosis": diag, "question_id": f"q-{label}"}


def test_summarise_counts():
    evals = [
        _eval_with_diagnosis(2.0, DOCS_HELPED),
        _eval_with_diagnosis(-1.0, EMPTY_RETRIEVAL),
        _eval_with_diagnosis(-2.0, LOW_RELEVANCE),
        _eval_with_diagnosis(-3.0, KNOWLEDGE_SUFFICIENT),
    ]
    summary = summarise_diagnoses(evals)
    assert summary["total"] == 4
    assert summary["counts"][DOCS_HELPED] == 1
    assert summary["counts"][EMPTY_RETRIEVAL] == 1
    assert summary["counts"][LOW_RELEVANCE] == 1
    assert summary["counts"][KNOWLEDGE_SUFFICIENT] == 1


def test_summarise_failures_list():
    evals = [
        _eval_with_diagnosis(5.0, DOCS_HELPED),
        _eval_with_diagnosis(-2.0, LOW_RELEVANCE),
    ]
    summary = summarise_diagnoses(evals)
    assert len(summary["failures"]) == 1
    assert summary["failures"][0]["delta"] == -2.0


def test_summarise_rates_sum_to_one():
    evals = [_eval_with_diagnosis(1.0, DOCS_HELPED)] * 3 + \
            [_eval_with_diagnosis(-1.0, EMPTY_RETRIEVAL)]
    summary = summarise_diagnoses(evals)
    total_rate = sum(summary["rates"].values())
    assert total_rate == pytest.approx(1.0)


def test_summarise_empty_list():
    summary = summarise_diagnoses([])
    assert summary["total"] == 0
    assert all(v == 0 for v in summary["rates"].values())


def test_no_context_answer_is_empty_retrieval():
    """A question whose context arm produced nothing has no retrieval to classify as relevant."""
    result = diagnose(None, -2.0)
    assert result["label"] == EMPTY_RETRIEVAL
    assert result["evidence"] == {"delta": -2.0, "doc_source": "", "retrieved_count": 0}


def test_summarise_failures_are_the_persisted_records():
    """The report prints each failure's own delta and diagnosis, so the records pass through."""
    evals = [_eval_with_diagnosis(-2.0, LOW_RELEVANCE)]
    assert summarise_diagnoses(evals)["failures"] == evals


def test_summarise_refuses_a_record_without_question_id():
    with pytest.raises(records.RecordError, match="missing question_id"):
        summarise_diagnoses([{"delta": -1.0, "diagnosis": None}])
