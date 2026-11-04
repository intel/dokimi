# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for RagasEvaluator."""

import pytest
from unittest.mock import Mock, patch

from dokimi.eval.ragas_eval import RagasEvaluator, RagasResult


@pytest.fixture
def sample_answers():
    """Minimal answer pairs from Answerer.generate_answers()."""
    return [
        {
            "question_id": "q_001",
            "question_text": "How to use parallel_for in oneTBB?",
            "with_docs": {
                "answer": "Use tbb::parallel_for(range, body) to parallelize loops.",
                "retrieved_docs": [
                    {"snippet": "tbb::parallel_for divides the range into chunks...",
                     "source": "context7"}
                ],
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
            "without_docs": {
                "answer": "parallel_for is a TBB construct for loop parallelism.",
                "token_usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
            },
        },
        {
            "question_id": "q_002",
            "question_text": "What is task_arena?",
            "with_docs": {
                "answer": "task_arena is a TBB class for controlling thread count.",
                "retrieved_docs": [
                    {"snippet": "tbb::task_arena controls the number of worker threads.",
                     "source": "context7"}
                ],
                "token_usage": {"prompt_tokens": 110, "completion_tokens": 45, "total_tokens": 155},
            },
            "without_docs": {
                "answer": "task_arena limits parallelism to a specific arena.",
                "token_usage": {"prompt_tokens": 55, "completion_tokens": 28, "total_tokens": 83},
            },
        },
    ]


class _FakeSeries:
    def __init__(self, values):
        self._values = values

    def dropna(self):
        return self

    def tolist(self):
        return self._values


class _FakeILoc:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, idx):
        return self._rows[idx]


class _FakeDataFrame:
    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []
        self.iloc = _FakeILoc(rows)

    def __getitem__(self, col):
        return _FakeSeries([row[col] for row in self._rows])


def _make_ragas_result_df(n_rows: int):
    """Build a fake RAGAS result dataframe without requiring pandas in CI."""
    return _FakeDataFrame([
        {
            "question": f"q{i}",
            "answer": f"a{i}",
            "contexts": ["ctx"],
            "faithfulness": 0.8,
            "answer_relevancy": 0.75,
            "context_precision": 0.9,
        }
        for i in range(n_rows)
    ])


class _FakeRagasResult:
    """Mimics ragas.evaluation.Result."""
    def __init__(self, df):
        self._df = df
    def to_pandas(self):
        return self._df


class TestRagasResult:
    def test_to_dict_structure(self):
        result = RagasResult(
            with_docs_scores={"q1": {"faithfulness": 0.8}},
            without_docs_scores={"q1": {"answer_relevancy": 0.7}},
            summary_with_docs={"faithfulness": 0.8, "answer_relevancy": 0.75},
            summary_without_docs={"answer_relevancy": 0.7},
            n_with=2,
            n_without=2,
        )
        d = result.to_dict()
        assert "ragas_summary" in d
        assert "with_docs" in d["ragas_summary"]
        assert "without_docs" in d["ragas_summary"]
        assert "ragas_per_question" in d
        assert "ragas_n_evaluated" in d

    def test_format_summary_contains_metrics(self):
        result = RagasResult(
            with_docs_scores={},
            without_docs_scores={},
            summary_with_docs={"faithfulness": 0.82, "answer_relevancy": 0.77},
            summary_without_docs={"answer_relevancy": 0.65},
            n_with=5,
            n_without=5,
        )
        text = result.format_summary()
        assert "faithfulness" in text
        assert "answer_relevancy" in text
        assert "0.82" in text
        assert "delta" in text

    def test_format_summary_no_without_docs(self):
        result = RagasResult(
            with_docs_scores={},
            without_docs_scores={},
            summary_with_docs={"faithfulness": 0.9},
            summary_without_docs={},
            n_with=3,
            n_without=0,
        )
        text = result.format_summary()
        assert "faithfulness" in text
        assert "delta" not in text


class TestRagasEvaluator:
    def _mock_evaluator(self, metrics=None):
        """Build a RagasEvaluator with mocked RAGAS internals."""
        mock_llm = Mock()
        mock_metric = Mock()
        mock_metric.llm = None

        with patch('dokimi.eval.ragas_eval.RagasEvaluator._build_ragas_llm',
                   return_value=mock_llm), \
             patch('dokimi.eval.ragas_eval.RagasEvaluator._build_metrics',
                   return_value=[mock_metric]), \
             patch('dokimi.eval.ragas_eval.RagasEvaluator._build_ragas_embeddings',
                   return_value=None):
            evaluator = RagasEvaluator(metrics=metrics or ["faithfulness", "answer_relevancy"])
        evaluator._ragas_llm = mock_llm
        evaluator._metric_objects = [mock_metric]
        return evaluator

    def test_evaluate_returns_ragas_result(self, sample_answers):
        evaluator = self._mock_evaluator()
        df = _make_ragas_result_df(2)
        fake_result = _FakeRagasResult(df)

        fake_dataset_cls = Mock()
        fake_dataset_cls.from_list.return_value = Mock()
        with patch('dokimi.eval.ragas_eval._import_ragas_runtime',
                   return_value=(fake_dataset_cls, Mock(return_value=fake_result))):
            result = evaluator.evaluate(sample_answers, include_without_docs=False)

        assert isinstance(result, RagasResult)

    def test_evaluate_skips_missing_context(self, sample_answers):
        """Answers without retrieved_docs should not appear in with_docs evaluation."""
        answers_no_ctx = [
            {
                "question_id": "q_empty",
                "question_text": "What is X?",
                "with_docs": {"answer": "X is...", "retrieved_docs": []},
                "without_docs": {"answer": "X is..."},
            }
        ]
        evaluator = self._mock_evaluator()
        df = _make_ragas_result_df(0)
        fake_result = _FakeRagasResult(df)

        fake_dataset_cls = Mock()
        fake_dataset_cls.from_list.return_value = Mock()
        with patch('dokimi.eval.ragas_eval._import_ragas_runtime',
                   return_value=(fake_dataset_cls, Mock(return_value=fake_result))):
            result = evaluator.evaluate(answers_no_ctx, include_without_docs=False)

        # Nothing evaluated (no context rows)
        assert result.n_with == 0

    def test_evaluate_handles_ragas_exception(self, sample_answers):
        """RAGAS exceptions should be caught and result in empty scores."""
        evaluator = self._mock_evaluator()

        fake_dataset_cls = Mock()
        fake_dataset_cls.from_list.return_value = Mock()
        with patch('dokimi.eval.ragas_eval._import_ragas_runtime',
                   return_value=(fake_dataset_cls, Mock(side_effect=Exception("RAGAS internal error")))):
            result = evaluator.evaluate(sample_answers, include_without_docs=False)

        assert result.summary_with_docs == {}

    def test_to_dict_embeddable_in_answers(self, sample_answers):
        """RagasResult.to_dict() should be JSON-serialisable."""
        import json
        result = RagasResult(
            with_docs_scores={"q_001": {"faithfulness": 0.9}},
            without_docs_scores={"q_001": {"answer_relevancy": 0.7}},
            summary_with_docs={"faithfulness": 0.9},
            summary_without_docs={"answer_relevancy": 0.7},
            n_with=1,
            n_without=1,
        )
        serialised = json.dumps(result.to_dict())
        assert "faithfulness" in serialised
