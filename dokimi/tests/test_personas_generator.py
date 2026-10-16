# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for PersonaGenerator."""

import sys
import types
import pytest
from unittest.mock import Mock, patch
import json
from datetime import datetime

# Provide mock langchain modules if not installed
for mod_name in ['langchain_openai', 'langchain_anthropic']:
    if mod_name not in sys.modules:
        mock_mod = types.ModuleType(mod_name)
        mock_mod.ChatOpenAI = Mock
        mock_mod.ChatAnthropic = Mock
        sys.modules[mod_name] = mock_mod

from dokimi.personas.generator import PersonaGenerator, PERSONA_GENERATION_PROMPT


@pytest.fixture
def sample_analysis():
    """Create sample analysis data."""
    return {
        "readme_content": "# oneTBB\n\nThreading Building Blocks for parallel programming...",
        "description": "Cross-platform C++ parallel programming library",
        "topics": ["parallel-computing", "cpp", "threading"],
        "use_cases": [
            "High-performance computing",
            "Data analytics pipelines",
            "Game engine parallelization"
        ],
        "issues_analysis": {
            "common_questions": [
                "How to use parallel_for?",
                "Best practices for task arenas?",
                "How to handle NUMA systems?"
            ],
            "common_labels": ["question", "help wanted", "performance"]
        },
        "api_patterns": [
            "tbb::parallel_for",
            "tbb::task_arena",
            "tbb::flow::graph"
        ]
    }


@pytest.fixture
def sample_llm_response():
    """Create sample LLM response with personas."""
    return {
        "personas": [
            {
                "id": "hpc_developer",
                "name": "HPC Developer",
                "description": "High-performance computing specialist",
                "skill_level": "advanced",
                "concerns": ["performance", "scalability", "NUMA"],
                "typical_questions": [
                    "How to minimize task overhead?",
                    "What's the best way to handle NUMA?"
                ]
            },
            {
                "id": "ml_engineer",
                "name": "ML Engineer",
                "description": "Machine learning engineer parallelizing training",
                "skill_level": "intermediate",
                "concerns": ["data parallelism", "GPU support", "batch processing"],
                "typical_questions": [
                    "How to parallelize data loading?",
                    "Can I use TBB with GPU?"
                ]
            },
            {
                "id": "cs_student",
                "name": "CS Student",
                "description": "Computer science student learning parallel programming",
                "skill_level": "beginner",
                "concerns": ["learning curve", "simple examples", "debugging"],
                "typical_questions": [
                    "How do I get started with TBB?",
                    "What's the difference between parallel_for and OpenMP?"
                ]
            }
        ]
    }


class TestPersonaGenerator:
    """Test suite for PersonaGenerator."""
    
    def test_init_openai(self):
        """Test initialization with OpenAI provider."""
        mock_cls = Mock()
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator(model="gpt-6.1-sol", provider="openai")
            assert generator.model == "gpt-6.1-sol"
            assert generator.provider == "openai"
            mock_cls.assert_called_once_with(model="gpt-6.1-sol", api_key=None)
    
    def test_init_anthropic(self):
        """Test initialization with Anthropic provider."""
        mock_cls = Mock()
        with patch('dokimi.personas.generator.ChatAnthropic', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator(model="claude-haiku", provider="anthropic", api_key="test-key")
            assert generator.model == "claude-haiku"
            assert generator.provider == "anthropic"
            mock_cls.assert_called_once_with(model="claude-haiku", api_key="test-key")
    
    def test_init_unsupported_provider(self):
        """Test initialization with unsupported provider."""
        mock_cls = Mock()
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            with pytest.raises(ValueError) as exc_info:
                PersonaGenerator(provider="unsupported")
            assert "Unsupported provider" in str(exc_info.value)
    
    def test_init_langchain_unavailable(self):
        """Test initialization when langchain is not available."""
        with patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', False):
            with pytest.raises(ImportError) as exc_info:
                PersonaGenerator()
            assert "LLM dependencies not available" in str(exc_info.value)
    
    def test_generate_personas_success(self, sample_analysis, sample_llm_response):
        """Test successful persona generation."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = json.dumps(sample_llm_response)
        mock_llm.invoke.return_value = mock_response
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator(model="gpt-6.1-sol")
            result = generator.generate_personas("oneTBB", sample_analysis, target_count=5)
        
        # Verify structure
        assert "product" in result
        assert "generated_at" in result
        assert "model" in result
        assert "provider" in result
        assert "personas" in result
        
        assert result["product"] == "oneTBB"
        assert result["model"] == "gpt-6.1-sol"
        assert result["provider"] == "openai"
        
        # Verify personas
        personas = result["personas"]
        assert len(personas) == 3
        assert personas[0]["id"] == "hpc_developer"
        assert personas[0]["skill_level"] == "advanced"
        assert "concerns" in personas[0]
        assert "typical_questions" in personas[0]
    
    def test_generate_personas_clamps_count(self, sample_analysis, sample_llm_response):
        """Test that persona count is clamped to 5-8 range."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = json.dumps(sample_llm_response)
        mock_llm.invoke.return_value = mock_response
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator()
            generator.generate_personas("oneTBB", sample_analysis, target_count=2)
            generator.generate_personas("oneTBB", sample_analysis, target_count=20)
    
    def test_generate_personas_invalid_json(self, sample_analysis):
        """Test handling of invalid JSON response from LLM."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = "This is not JSON"
        mock_llm.invoke.return_value = mock_response
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator()
            with pytest.raises((json.JSONDecodeError, ValueError)):
                generator.generate_personas("oneTBB", sample_analysis)
    
    def test_generate_personas_missing_key(self, sample_analysis):
        """Test handling of response missing 'personas' key."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = json.dumps({"wrong_key": []})
        mock_llm.invoke.return_value = mock_response
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator()
            with pytest.raises(ValueError) as exc_info:
                generator.generate_personas("oneTBB", sample_analysis)
            assert "missing 'personas' key" in str(exc_info.value)
    
    def test_generate_personas_invalid_structure(self, sample_analysis):
        """Test handling of persona with missing required fields."""
        mock_llm = Mock()
        mock_response = Mock()
        invalid_response = {
            "personas": [{"id": "test", "name": "Test", "description": "Test persona"}]
        }
        mock_response.content = json.dumps(invalid_response)
        mock_llm.invoke.return_value = mock_response
        mock_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.personas.generator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.personas.generator.LANGCHAIN_AVAILABLE', True):
            generator = PersonaGenerator()
            with pytest.raises(ValueError) as exc_info:
                generator.generate_personas("oneTBB", sample_analysis)
            assert "missing required fields" in str(exc_info.value)
    
    def test_summarize_readme(self):
        """Test README summarization."""
        generator = PersonaGenerator.__new__(PersonaGenerator)
        
        short_readme = "Short content"
        assert generator._summarize_readme(short_readme) == short_readme
        
        long_readme = "A" * 2000
        summary = generator._summarize_readme(long_readme, max_length=1000)
        assert len(summary) <= 1100  # max_length + some buffer for sentence boundary
        
        # Empty readme
        assert generator._summarize_readme("") == ""
    
    def test_get_timestamp(self):
        """Test timestamp generation."""
        generator = PersonaGenerator.__new__(PersonaGenerator)
        timestamp = generator._get_timestamp()
        
        # Should be valid ISO format with Z suffix
        assert timestamp.endswith("Z")
        # Should be parseable
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    
    def test_save_personas(self, tmp_path, sample_llm_response):
        """Test saving personas to file."""
        generator = PersonaGenerator.__new__(PersonaGenerator)
        
        personas_data = {
            "product": "oneTBB",
            "generated_at": "2026-02-20T00:00:00Z",
            "model": "gpt-6.1-sol",
            "provider": "openai",
            "personas": sample_llm_response["personas"]
        }
        
        output_path = tmp_path / "personas" / "oneTBB.json"
        generator.save_personas(personas_data, output_path)
        
        assert output_path.exists()
        
        loaded = json.loads(output_path.read_text())
        assert loaded["product"] == "oneTBB"
        assert len(loaded["personas"]) == 3
    
    def test_load_personas(self, tmp_path, sample_llm_response):
        """Test loading personas from file."""
        generator = PersonaGenerator.__new__(PersonaGenerator)
        
        personas_data = {
            "product": "oneTBB",
            "personas": sample_llm_response["personas"]
        }
        
        input_path = tmp_path / "personas.json"
        input_path.write_text(json.dumps(personas_data))
        
        loaded = generator.load_personas(input_path)
        assert loaded["product"] == "oneTBB"
        assert len(loaded["personas"]) == 3


class TestPersonaGenerationPrompt:
    """Test the LLM prompt template."""
    
    def test_prompt_contains_placeholders(self):
        """Test that prompt has all required placeholders."""
        required_placeholders = [
            "{library_name}",
            "{description}",
            "{topics}",
            "{readme_summary}",
            "{use_cases}",
            "{common_questions}",
            "{api_patterns}"
        ]
        
        for placeholder in required_placeholders:
            assert placeholder in PERSONA_GENERATION_PROMPT
    
    def test_prompt_format(self, sample_analysis):
        """Test that prompt can be formatted with sample data."""
        formatted = PERSONA_GENERATION_PROMPT.format(
            library_name="oneTBB",
            description=sample_analysis["description"],
            topics=", ".join(sample_analysis["topics"]),
            readme_summary=sample_analysis["readme_content"][:500],
            use_cases="\n".join(f"- {uc}" for uc in sample_analysis["use_cases"]),
            common_questions="\n".join(f"- {q}" for q in sample_analysis["issues_analysis"]["common_questions"]),
            api_patterns="\n".join(f"- {ap}" for ap in sample_analysis["api_patterns"])
        )
        
        assert "oneTBB" in formatted
        assert "parallel-computing" in formatted
        assert "High-performance computing" in formatted
