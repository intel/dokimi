# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for Answerer."""

import sys
import types
import pytest
import json
from unittest.mock import Mock, patch

# Mock langchain modules
for mod_name in ['langchain_openai', 'langchain_anthropic']:
    if mod_name not in sys.modules:
        mock_mod = types.ModuleType(mod_name)
        mock_mod.ChatOpenAI = Mock
        mock_mod.ChatAnthropic = Mock
        sys.modules[mod_name] = mock_mod

from dokimi.eval.answerer import Answerer, ANSWER_PROMPT_WITH_DOCS, ANSWER_PROMPT_WITHOUT_DOCS

# Canonical return value for llm_call_with_usage mock
_LLM_MOCK_RETURN = ("This is a test answer from LLM.", {
    "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15
})


@pytest.fixture
def sample_questions():
    return [
        {
            "id": "q_001",
            "text": "How to use parallel_for in oneTBB?",
            "personas": ["hpc_developer"],
            "topics": ["parallel_for"]
        },
        {
            "id": "q_002",
            "text": "What is task_arena?",
            "personas": ["cs_student"],
            "topics": ["task_arena"]
        }
    ]


@pytest.fixture
def mock_mcp_client():
    client = Mock()
    client.get_library_docs.return_value = [
        {
            "content": "parallel_for documentation: Use tbb::parallel_for(range, lambda) to parallelize loops.",
            "source": "context7",
            "library_id": "uxlfoundation/oneTBB"
        }
    ]
    return client


@pytest.fixture
def mock_llm():
    """Legacy fixture kept for backwards compatibility with tests that still use it."""
    llm = Mock()
    resp = Mock()
    resp.content = "This is a test answer from LLM."
    llm.invoke.return_value = resp
    return llm


@pytest.fixture
def mock_llm_call():
    """Patch llm_call_with_usage so tests don't hit real APIs."""
    with patch('dokimi.eval.answerer.llm_call_with_usage', return_value=_LLM_MOCK_RETURN) as m:
        yield m


class TestAnswererInit:
    def test_init_openai(self):
        mock_cls = Mock()
        with patch('dokimi.eval.answerer.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(model="gpt-4o", provider="openai")
            assert answerer.model == "gpt-4o"
            assert answerer.provider == "openai"
            mock_cls.assert_called_once()

    def test_init_anthropic(self):
        mock_cls = Mock()
        with patch('dokimi.eval.answerer.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(model="claude-opus", provider="anthropic", api_key="test")
            # The anthropic client is the one built, with the caller's model and key.
            mock_cls.assert_called_once_with(model="claude-opus", api_key="test")
            assert answerer.llm is mock_cls.return_value
            assert answerer.provider == "anthropic"

    def test_init_unsupported_provider(self):
        mock_cls = Mock()
        with patch('dokimi.eval.answerer.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            with pytest.raises(ValueError) as exc:
                Answerer(provider="unsupported")
            assert "Unsupported provider" in str(exc.value)

    def test_init_no_langchain(self):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', False):
            with pytest.raises(ImportError) as exc:
                Answerer()
            assert "LLM dependencies not available" in str(exc.value)


class TestGenerateAnswers:
    def test_returns_list(self, sample_questions, mock_mcp_client, mock_llm_call):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions)

            assert isinstance(answers, list)
            assert len(answers) == len(sample_questions)

    def test_includes_both_modes(self, sample_questions, mock_mcp_client, mock_llm_call):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions)

            for ans in answers:
                assert "with_docs" in ans
                assert "without_docs" in ans
                assert "question_id" in ans
                assert "question_text" in ans

    def test_no_mcp_client_skips_with_docs(self, sample_questions, mock_llm_call):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer()  # No MCP client
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions)

            assert answers[0]["with_docs"] is None
            assert answers[0]["without_docs"] is not None

    def test_error_handling(self, sample_questions, mock_mcp_client):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.eval.answerer.llm_call_with_usage',
                   side_effect=Exception("LLM error")):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions)

            # Should not crash, should include error field
            assert len(answers) == len(sample_questions)
            assert "error" in answers[0]

    def test_token_usage_in_answers(self, sample_questions, mock_mcp_client, mock_llm_call):
        """Each answer should include token_usage for both modes."""
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions[:1])

            ans = answers[0]
            assert "token_usage" in ans["with_docs"]
            assert "token_usage" in ans["without_docs"]
            assert ans["with_docs"]["token_usage"]["total_tokens"] == 15
            assert ans["without_docs"]["token_usage"]["total_tokens"] == 15

    def test_empty_retrieval_fallback_includes_telemetry(self, sample_questions, mock_llm_call):
        client = Mock()
        client.get_library_docs.return_value = []

        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions[:1])

            with_docs = answers[0]["with_docs"]
            assert with_docs["token_usage"]["total_tokens"] == 0
            assert with_docs["token_usage"]["context_chars"] == 0
            assert with_docs["metrics"]["n_calls"] == 0
            assert with_docs["metrics"]["cost_usd"] == 0.0

    def test_token_usage_summary_in_output(self, sample_questions, mock_mcp_client, mock_llm_call, tmp_path):
        """Output file should include token_usage_summary with with_docs/without_docs breakdown."""
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions[:1])

            out_path = tmp_path / "answers.json"
            answerer.save_answers(answers, out_path)
            data = json.loads(out_path.read_text())

            assert "token_usage_summary" in data
            summary = data["token_usage_summary"]
            assert "with_docs" in summary
            assert "without_docs" in summary
            assert summary["total_tokens"] > 0


class TestRetrieveDocs:
    def test_calls_mcp_client(self, mock_mcp_client):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            docs = answerer._retrieve_docs("uxlfoundation/oneTBB", "parallel_for", 4000)

            mock_mcp_client.get_library_docs.assert_called_once_with(
                library_id="uxlfoundation/oneTBB", query="parallel_for", max_tokens=4000
            )
            assert [d["content"] for d in docs] == [
                mock_mcp_client.get_library_docs.return_value[0]["content"]
            ]

    def test_error_handling(self, mock_mcp_client):
        mock_mcp_client.get_library_docs.side_effect = Exception("Network error")

        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            docs = answerer._retrieve_docs("uxlfoundation/oneTBB", "test question", 4000)

            assert docs == []


class TestGenerateWithDocs:
    def test_with_docs(self, mock_llm_call):
        docs = [{"content": "Test doc content", "source": "context7"}]

        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer()
            result = answerer._generate_with_docs("Test question?", docs)

            assert "answer" in result
            assert "retrieved_docs" in result
            assert "model" in result
            assert "token_usage" in result
            assert result["doc_source"] == "context7"
            assert result["answer"] == "This is a test answer from LLM."

    def test_empty_docs(self):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer()
            result = answerer._generate_with_docs("Test question?", [])

            assert result["answer"] == "[No documentation retrieved]"
            assert result["retrieved_docs"] == []
            assert "token_usage" in result


class TestGenerateWithoutDocs:
    def test_generates_answer(self, mock_llm_call):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer()
            result = answerer._generate_without_docs("Test question?")

            assert "answer" in result
            assert "model" in result
            assert "token_usage" in result
            assert result["answer"] == "This is a test answer from LLM."


class TestSaveLoadAnswers:
    def test_save_answers(self, tmp_path, sample_questions, mock_mcp_client, mock_llm_call):
        with patch('dokimi.eval.answerer.LANGCHAIN_AVAILABLE', True):
            answerer = Answerer(mcp_client=mock_mcp_client)
            answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", sample_questions[:1])

            output_path = tmp_path / "answers.json"
            answerer.save_answers(answers, output_path)

            assert output_path.exists()
            data = json.loads(output_path.read_text())
            assert "generated_at" in data
            assert "model" in data
            assert "answers" in data
            assert "token_usage_summary" in data
            assert data["total_questions"] == 1

    def test_load_answers(self, tmp_path):
        test_data = {
            "generated_at": "2026-02-21T00:00:00Z",
            "model": "gpt-4o",
            "total_questions": 1,
            "answers": [{"question_id": "q_001", "question_text": "Test?"}]
        }

        input_path = tmp_path / "answers.json"
        input_path.write_text(json.dumps(test_data))

        loaded = Answerer.load_answers(input_path)
        assert loaded["model"] == "gpt-4o"
        assert len(loaded["answers"]) == 1


class TestPromptTemplates:
    def test_with_docs_template(self):
        assert "{question}" in ANSWER_PROMPT_WITH_DOCS
        assert "{docs}" in ANSWER_PROMPT_WITH_DOCS

    def test_without_docs_template(self):
        assert "{question}" in ANSWER_PROMPT_WITHOUT_DOCS
