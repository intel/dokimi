# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for QuestionGenerator."""

import sys
import types
import pytest
import json
from unittest.mock import Mock, patch

# Mock langchain modules before importing
for mod_name in ['langchain_openai', 'langchain_anthropic']:
    if mod_name not in sys.modules:
        mock_mod = types.ModuleType(mod_name)
        mock_mod.ChatOpenAI = Mock
        mock_mod.ChatAnthropic = Mock
        sys.modules[mod_name] = mock_mod

from dokimi.questions.llm_gen import QuestionGenerator, QUESTION_GENERATION_PROMPT


@pytest.fixture
def sample_personas():
    return [
        {
            "id": "hpc_developer",
            "name": "HPC Developer",
            "description": "High-performance computing specialist",
            "skill_level": "advanced",
            "concerns": ["performance", "scalability"]
        },
        {
            "id": "cs_student",
            "name": "CS Student",
            "description": "Learning parallel programming",
            "skill_level": "beginner",
            "concerns": ["learning curve", "simple examples"]
        }
    ]


@pytest.fixture
def sample_topics():
    return ["parallel_for", "task_arena", "flow_graph"]


@pytest.fixture
def mock_llm():
    llm = Mock()
    resp = Mock()
    resp.content = json.dumps(["Question 1?", "Question 2?"])
    llm.invoke.return_value = resp
    return llm


class TestQuestionGeneratorInit:
    def test_init_openai(self):
        mock_cls = Mock()
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator(model="gpt-4o-mini", provider="openai")
            assert gen.model == "gpt-4o-mini"
            assert gen.provider == "openai"
            mock_cls.assert_called_once()
    
    def test_init_anthropic(self):
        mock_cls = Mock()
        with patch('dokimi.questions.llm_gen.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator(model="claude-haiku", provider="anthropic", api_key="test")
            assert gen.model == "claude-haiku"
            assert gen.provider == "anthropic"
    
    def test_init_unsupported_provider(self):
        mock_cls = Mock()
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            with pytest.raises(ValueError) as exc:
                QuestionGenerator(provider="unsupported")
            assert "Unsupported provider" in str(exc.value)
    
    def test_init_no_langchain(self):
        with patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', False):
            with pytest.raises(ImportError) as exc:
                QuestionGenerator()
            assert "LLM dependencies not available" in str(exc.value)


class TestGenerateQuestions:
    def test_returns_list(self, sample_personas, sample_topics, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            questions = gen.generate_questions("oneTBB", sample_personas, sample_topics, questions_per_topic=2)
            assert isinstance(questions, list)
            assert len(questions) > 0
    
    def test_assigns_unique_ids(self, sample_personas, sample_topics, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            questions = gen.generate_questions("oneTBB", sample_personas, sample_topics)
            ids = [q["id"] for q in questions]
            assert len(ids) == len(set(ids))  # All unique
            assert all(qid.startswith("q_") for qid in ids)
    
    def test_includes_required_fields(self, sample_personas, sample_topics, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            questions = gen.generate_questions("oneTBB", sample_personas, sample_topics)
            for q in questions:
                assert "id" in q
                assert "text" in q
                assert "personas" in q
                assert "difficulty" in q
                assert "topics" in q
                assert "metadata" in q
    
    def test_generates_for_each_persona(self, sample_personas, sample_topics, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            questions = gen.generate_questions("oneTBB", sample_personas, sample_topics)
            
            persona_ids = {q["personas"][0] for q in questions}
            assert "hpc_developer" in persona_ids
            assert "cs_student" in persona_ids


class TestCallLLM:
    def test_parses_json_array(self, sample_personas):
        mock_llm = Mock()
        mock_resp = Mock()
        mock_resp.content = '["Q1?", "Q2?"]'
        mock_llm.invoke.return_value = mock_resp
        
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            result = gen._call_llm("oneTBB", sample_personas[0], "parallel_for", count=2)
            assert result == ["Q1?", "Q2?"]
    
    def test_invalid_json_raises(self, sample_personas):
        mock_llm = Mock()
        mock_resp = Mock()
        mock_resp.content = "Not JSON"
        mock_llm.invoke.return_value = mock_resp
        
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            with pytest.raises(ValueError):
                gen._call_llm("oneTBB", sample_personas[0], "parallel_for", count=2)
    
    def test_prompt_includes_persona_details(self, sample_personas):
        mock_llm = Mock()
        mock_resp = Mock()
        mock_resp.content = '["Q1?"]'
        mock_llm.invoke.return_value = mock_resp
        
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            gen._call_llm("oneTBB", sample_personas[0], "parallel_for", count=1)
            
            # Check that invoke was called with prompt containing persona details
            call_args = mock_llm.invoke.call_args[0][0]
            assert "HPC Developer" in call_args
            assert "advanced" in call_args
            assert "parallel_for" in call_args


class TestSaveQuestions:
    def test_saves_json(self, tmp_path, sample_personas, sample_topics, mock_llm):
        mock_cls = Mock(return_value=mock_llm)
        with patch('dokimi.questions.llm_gen.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.llm_gen.LANGCHAIN_AVAILABLE', True):
            gen = QuestionGenerator()
            questions = gen.generate_questions("oneTBB", sample_personas[:1], sample_topics[:1])
            
            output_path = tmp_path / "questions.json"
            gen.save_questions(questions, output_path)
            
            assert output_path.exists()
            data = json.loads(output_path.read_text())
            assert "generated_at" in data
            assert "model" in data
            assert "questions" in data
            assert data["total_questions"] == len(questions)


class TestPromptTemplate:
    def test_has_required_placeholders(self):
        # Prompt now uses __PLACEHOLDER__ style (safe from .format() conflicts)
        required = [
            "__LIBRARY_NAME__", "__PERSONA_NAME__", "__SKILL_LEVEL__",
            "__PERSONA_DESCRIPTION__", "__CONCERNS__", "__TOPIC__", "__COUNT__"
        ]
        for placeholder in required:
            assert placeholder in QUESTION_GENERATION_PROMPT
