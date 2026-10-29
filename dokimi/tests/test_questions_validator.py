# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for QuestionValidator."""

import sys
import types
import pytest
import json
from unittest.mock import Mock, patch

# Mock modules before importing
for mod_name in ['langchain_openai', 'langchain_anthropic', 'openai']:
    if mod_name not in sys.modules:
        mock_mod = types.ModuleType(mod_name)
        if 'langchain' in mod_name:
            mock_mod.ChatOpenAI = Mock
            mock_mod.ChatAnthropic = Mock
        else:  # openai
            mock_mod.OpenAI = Mock
        sys.modules[mod_name] = mock_mod

from dokimi.questions.validator import (
    EMBEDDING_BACKENDS,
    QuestionValidator,
    VALIDATION_PROMPT,
)


@pytest.fixture
def sample_questions():
    return [
        {
            "id": "q_001",
            "text": "How to use parallel_for in oneTBB?",
            "personas": ["hpc_developer"],
            "difficulty": "beginner",
            "topics": ["parallel_for"]
        },
        {
            "id": "q_002",
            "text": "What is task_arena?",
            "personas": ["cs_student"],
            "difficulty": "beginner",
            "topics": ["task_arena"]
        },
        {
            "id": "q_003",
            "text": "How do I use parallel_for?",  # Duplicate of q_001
            "personas": ["ml_engineer"],
            "difficulty": "intermediate",
            "topics": ["parallel_for"]
        }
    ]


@pytest.fixture
def mock_llm_validator():
    llm = Mock()
    resp = Mock()
    resp.content = json.dumps({
        "relevance": 85,
        "answerability": 90,
        "specificity": 80,
        "aggregate": 85,
        "reasoning": "Test"
    })
    llm.invoke.return_value = resp
    return llm


@pytest.fixture
def mock_openai_embeddings():
    client = Mock()
    resp = Mock()
    resp.data = [
        type('obj', (), {'embedding': [0.1]*384}),
        type('obj', (), {'embedding': [0.2]*384}),
        type('obj', (), {'embedding': [0.15]*384})  # Similar to first
    ]
    client.embeddings.create.return_value = resp
    return client


class TestQuestionValidatorInit:
    def test_init_defaults(self):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.ChatOpenAI', Mock(), create=True), \
             patch('dokimi.questions.validator.OpenAI', Mock(), create=True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}):
            # Pinned to the OpenAI backend: under "auto" the result would
            # otherwise depend on whether sentence-transformers is installed.
            val = QuestionValidator()
            assert val.threshold == 60
            assert val.embedding_provider == "openai"
            assert val.similarity_threshold == EMBEDDING_BACKENDS["openai"][1]

    def test_init_custom(self):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.ChatOpenAI', Mock(), create=True), \
             patch('dokimi.questions.validator.OpenAI', Mock(), create=True):
            val = QuestionValidator(threshold=70, similarity_threshold=0.90)
        # A 65 passes the default threshold of 60 but not a custom 70; the stats carry the
        # custom similarity threshold rather than the backend default.
        with patch.object(val, "_validate_question", return_value={"aggregate": 65}), \
             patch.object(val, "_deduplicate", side_effect=lambda qs: (qs, [])):
            validated, stats = val.validate_and_dedupe(
                "oneTBB", [{"id": "q", "text": "How do I use parallel_for?"}])
        assert (validated, stats["removed_low_score"]) == ([], 1)
        assert stats["similarity_threshold"] == 0.90
    
    def test_init_no_langchain(self):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False):
            val = QuestionValidator()
            assert val.llm is None
            assert val.openai_client is None


class TestValidateQuestion:
    def test_returns_score_dict(self, mock_llm_validator):
        mock_cls = Mock(return_value=mock_llm_validator)
        with patch('dokimi.questions.validator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False):
            val = QuestionValidator()
            score = val._validate_question("oneTBB", "How to use parallel_for?")
            
            assert isinstance(score, dict)
            assert "relevance" in score
            assert "answerability" in score
            assert "specificity" in score
            assert "aggregate" in score
    
    def test_no_llm_returns_default_score(self):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False):
            val = QuestionValidator()
            score = val._validate_question("oneTBB", "Test question?")
            
            assert score["aggregate"] == 100  # Default pass-through
    
    def test_invalid_json_returns_none(self, mock_llm_validator):
        mock_llm_validator.invoke.return_value.content = "Not JSON"
        mock_cls = Mock(return_value=mock_llm_validator)
        
        with patch('dokimi.questions.validator.ChatOpenAI', mock_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False):
            val = QuestionValidator()
            score = val._validate_question("oneTBB", "Test?")
            assert score["aggregate"] == 100


class TestValidateAndDedupe:
    def test_filters_low_scores(self, sample_questions, mock_llm_validator, mock_openai_embeddings):
        # First question scores high, second scores low
        responses = [
            json.dumps({"relevance": 90, "answerability": 90, "specificity": 85, "aggregate": 88, "reasoning": ""}),
            json.dumps({"relevance": 50, "answerability": 55, "specificity": 40, "aggregate": 48, "reasoning": ""}),
            json.dumps({"relevance": 85, "answerability": 85, "specificity": 80, "aggregate": 83, "reasoning": ""})
        ]
        mock_llm_validator.invoke.side_effect = [
            type('r', (), {'content': resp}) for resp in responses
        ]
        
        mock_openai_cls = Mock(return_value=mock_openai_embeddings)
        mock_llm_cls = Mock(return_value=mock_llm_validator)
        
        with patch('dokimi.questions.validator.ChatOpenAI', mock_llm_cls, create=True), \
             patch('dokimi.questions.validator.OpenAI', mock_openai_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}):
            val = QuestionValidator(threshold=60)
            validated, stats = val.validate_and_dedupe("oneTBB", sample_questions)
            
            # Second question should be filtered out (score 48 < 60)
            assert stats["after_validation"] == 2
            assert stats["removed_low_score"] == 1
    
    def test_deduplicates_similar_questions(self, sample_questions):
        # Mock embeddings where q_001 and q_003 are similar
        mock_embeddings = Mock()
        resp = Mock()
        resp.data = [
            type('obj', (), {'embedding': [1.0] + [0.0]*383}),  # q_001
            type('obj', (), {'embedding': [0.0, 1.0] + [0.0]*382}),  # q_002 (different)
            type('obj', (), {'embedding': [0.99] + [0.0]*383})  # q_003 (very similar to q_001)
        ]
        mock_embeddings.embeddings.create.return_value = resp
        
        mock_llm = Mock()
        mock_llm.invoke.return_value.content = json.dumps({
            "relevance": 85, "answerability": 85, "specificity": 80, "aggregate": 83, "reasoning": ""
        })
        
        mock_openai_cls = Mock(return_value=mock_embeddings)
        mock_llm_cls = Mock(return_value=mock_llm)
        
        with patch('dokimi.questions.validator.ChatOpenAI', mock_llm_cls, create=True), \
             patch('dokimi.questions.validator.OpenAI', mock_openai_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}), \
             patch.dict('sys.modules', {'numpy': None}):
            val = QuestionValidator(similarity_threshold=0.85)
            validated, stats = val.validate_and_dedupe("oneTBB", sample_questions)
            
            # Should deduplicate q_001 and q_003
            assert stats["after_deduplication"] < stats["after_validation"]
    
    def test_returns_stats(self, sample_questions, mock_llm_validator, mock_openai_embeddings):
        mock_llm_validator.invoke.return_value.content = json.dumps({
            "relevance": 85, "answerability": 85, "specificity": 80, "aggregate": 83, "reasoning": ""
        })
        
        mock_openai_cls = Mock(return_value=mock_openai_embeddings)
        mock_llm_cls = Mock(return_value=mock_llm_validator)
        
        with patch('dokimi.questions.validator.ChatOpenAI', mock_llm_cls, create=True), \
             patch('dokimi.questions.validator.OpenAI', mock_openai_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}):
            val = QuestionValidator()
            validated, stats = val.validate_and_dedupe("oneTBB", sample_questions)
            
            assert "initial_count" in stats
            assert "after_validation" in stats
            assert "after_deduplication" in stats
            assert "removed_low_score" in stats
            assert "removed_duplicates" in stats


class TestDeduplicate:
    def test_no_openai_returns_original(self, sample_questions):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False):
            val = QuestionValidator()
            unique, dup_groups = val._deduplicate(sample_questions)
            
            assert len(unique) == len(sample_questions)
            assert dup_groups == []
    
    def test_merges_personas_in_duplicates(self):
        # Test that when questions are duplicated, personas are merged
        questions = [
            {"id": "q_001", "text": "Question A", "personas": ["p1"]},
            {"id": "q_002", "text": "Question A", "personas": ["p2"]}
        ]
        
        # Mock embeddings that are identical
        mock_embeddings = Mock()
        resp = Mock()
        resp.data = [
            type('obj', (), {'embedding': [1.0]*384}),
            type('obj', (), {'embedding': [1.0]*384})
        ]
        mock_embeddings.embeddings.create.return_value = resp
        
        mock_openai_cls = Mock(return_value=mock_embeddings)
        
        with patch('dokimi.questions.validator.OpenAI', mock_openai_cls, create=True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}), \
             patch.dict('sys.modules', {'numpy': None}):
            val = QuestionValidator(similarity_threshold=0.99)
            unique, dup_groups = val._deduplicate(questions)
            
            # Should keep one, merge personas
            assert len(unique) == 1
            assert set(unique[0]["personas"]) == {"p1", "p2"}


class TestEmbeddingBackend:
    """The backend choice and the similarity threshold travel together."""

    def test_auto_prefers_openai_when_key_present(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch('dokimi.questions.validator.OpenAI', Mock(), create=True), \
             patch.dict('os.environ', {'OPENAI_API_KEY': 'dummy'}):
            val = QuestionValidator()
            assert val.embedding_provider == "openai"
            assert val.embedding_model == EMBEDDING_BACKENDS["openai"][0]

    def test_auto_falls_back_to_local_without_key(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator()
            assert val.embedding_provider == "local"
            assert val.embedding_model == EMBEDDING_BACKENDS["local"][0]
            assert val.openai_client is None
            # Dedupe must be considered available — that is the point of the
            # fallback: no key no longer means no deduplication.
            assert val._embeddings_available() is True

    def test_auto_reports_none_when_nothing_available(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False), \
             patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator()
            assert val.embedding_provider == "none"
            assert val._embeddings_available() is False

    def test_threshold_defaults_per_backend_and_can_be_overridden(self):
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True):
            assert (QuestionValidator(embedding_provider="local").similarity_threshold
                    == EMBEDDING_BACKENDS["local"][1])
            assert QuestionValidator(embedding_provider="local",
                                     similarity_threshold=0.42).similarity_threshold == 0.42

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError, match="embedding_provider"):
            QuestionValidator(embedding_provider="word2vec")

    def test_local_backend_requires_the_optional_package(self):
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False):
            with pytest.raises(ImportError, match="sentence-transformers"):
                QuestionValidator(embedding_provider="local")

    def test_local_embeddings_deduplicate_without_any_api_key(self, sample_questions):
        """q_001 and q_003 collapse using only the local encoder."""
        vectors = [[1.0, 0.0], [0.0, 1.0], [0.99, 0.01]]
        encoder = Mock()
        encoder.encode.return_value = vectors

        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch('dokimi.questions.validator.SentenceTransformer',
                   Mock(return_value=encoder), create=True), \
             patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator(embedding_provider="local", similarity_threshold=0.9)
            unique, dup_groups = val._deduplicate(sample_questions)

            assert len(unique) == 2
            assert len(dup_groups) == 1
            encoder.encode.assert_called_once()


class TestNoOpVisibility:
    """A run that scored nothing must not look like a run that scored everything."""

    def test_stats_flag_unvalidated_and_undeduplicated_runs(self, sample_questions):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False):
            val = QuestionValidator()
            kept, stats = val.validate_and_dedupe("oneTBB", sample_questions)

            assert len(kept) == len(sample_questions)
            assert stats["unscored_questions"] == len(sample_questions)
            assert stats["validation_performed"] is False
            assert stats["deduplication_performed"] is False
            assert stats["embedding_provider"] == "none"

    def test_scored_run_reports_performed(self, sample_questions, mock_llm_validator):
        mock_llm_cls = Mock(return_value=mock_llm_validator)
        with patch('dokimi.questions.validator.ChatOpenAI', mock_llm_cls, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False):
            val = QuestionValidator()
            _, stats = val.validate_and_dedupe("oneTBB", sample_questions)

            assert stats["unscored_questions"] == 0
            assert stats["validation_performed"] is True


class TestPromptTemplate:
    def test_has_required_placeholders(self):
        required = ["{library_name}", "{question}"]
        for placeholder in required:
            assert placeholder in VALIDATION_PROMPT


class TestCliStatsLines:
    """The CLI lines must describe what happened, including when nothing did."""

    @staticmethod
    def _stats(**over):
        base = {"initial_count": 3, "after_prefilter": 3, "after_validation": 3,
                "after_deduplication": 3, "removed_low_score": 0,
                "removed_duplicates": 0, "embedding_provider": "local",
                "embedding_model": "m", "similarity_threshold": 0.85,
                "unscored_questions": 0, "validation_performed": True,
                "deduplication_performed": True}
        base.update(over)
        return base

    def test_empty_input_is_not_reported_as_passed_through(self, capsys):
        from dokimi.commands.questions import print_validation_stats

        # The real stats for an empty run, not a hand-built dict.
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', False):
            val = QuestionValidator()
            _, stats = val.validate_and_dedupe("lib", [])
        print_validation_stats(stats)
        out = capsys.readouterr().out
        assert "No questions left to validate" in out
        assert "passed through unvalidated" not in out

    def test_unscored_run_still_warns(self, capsys):
        from dokimi.commands.questions import print_validation_stats

        print_validation_stats(self._stats(unscored_questions=3,
                                           validation_performed=False))
        assert "NO questions were scored" in capsys.readouterr().out

    def test_partial_failure_reports_the_count(self, capsys):
        from dokimi.commands.questions import print_validation_stats

        print_validation_stats(self._stats(unscored_questions=1))
        assert "1 questions passed through unscored" in capsys.readouterr().out

    def test_clean_run_prints_no_warning(self, capsys):
        from dokimi.commands.questions import print_validation_stats

        print_validation_stats(self._stats())
        assert "⚠" not in capsys.readouterr().out


class TestDedupeRobustness:
    """Round-2 review: a local backend must not turn a skipped step into a crash,
    and curated questions must survive a merge."""

    @staticmethod
    def _local(encoder):
        return patch('dokimi.questions.validator.SentenceTransformer',
                     Mock(return_value=encoder), create=True)

    def test_a_backend_failure_skips_dedupe_instead_of_aborting(self, sample_questions):
        encoder = Mock()
        encoder.encode.side_effect = OSError("We couldn't connect to 'https://huggingface.co'")
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder), patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator(embedding_provider="local")
            unique, groups = val._deduplicate(sample_questions)
        assert unique == sample_questions and groups == []
        assert val.dedupe_ran is False
        assert "huggingface.co" in val.dedupe_error

    def test_stats_report_the_failed_dedupe_not_the_configured_backend(self, sample_questions):
        encoder = Mock()
        encoder.encode.side_effect = OSError("model load failed")
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder), patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator(embedding_provider="local")
            _, stats = val.validate_and_dedupe("lib", sample_questions)
        assert stats["deduplication_performed"] is False
        assert "model load failed" in stats["deduplication_error"]

    def test_a_bug_is_not_mistaken_for_a_backend_failure(self, sample_questions):
        """Only backend errors skip dedupe; a TypeError from bad input still raises."""
        encoder = Mock()
        encoder.encode.side_effect = TypeError("Unsupported input type: int")
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder):
            val = QuestionValidator(embedding_provider="local")
            with pytest.raises(TypeError):
                val._deduplicate(sample_questions)

    def test_personas_go_to_the_kept_question_when_ids_are_missing(self):
        """Matching the kept question back by id sent its personas to the first
        id-less question in the list (None == None)."""
        questions = [{"text": "What is the capital of France?", "personas": ["d"]},
                     {"text": "How do I use parallel_for?", "personas": ["b"], "source_type": "manual"},
                     {"text": "How exactly do I use parallel_for here?", "personas": ["a"]}]
        encoder = Mock()
        encoder.encode.return_value = [[0.0, 1.0], [1.0, 0.0], [0.99, 0.01]]
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder):
            val = QuestionValidator(embedding_provider="local", similarity_threshold=0.9)
            kept, _ = val._deduplicate(questions, protect=lambda q: q.get("source_type") == "manual")
        by_text = {q["text"]: set(q["personas"]) for q in kept}
        assert by_text["How do I use parallel_for?"] == {"a", "b"}
        assert by_text["What is the capital of France?"] == {"d"}

    def test_a_protected_question_is_never_the_one_dropped(self):
        """The merge keeps the longest text; a manual golden question must not be
        replaced by a longer generated paraphrase of it."""
        questions = [
            {"id": "gold", "text": "How do I use parallel_for?", "source_type": "manual"},
            {"id": "gen", "text": "How exactly do I use parallel_for in my code?",
             "source_type": "generated"},
        ]
        encoder = Mock()
        encoder.encode.return_value = [[1.0, 0.0], [0.99, 0.01]]
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder):
            val = QuestionValidator(embedding_provider="local", similarity_threshold=0.9)
            unprotected, _ = val._deduplicate([dict(q) for q in questions])
            protected, groups = val._deduplicate(
                [dict(q) for q in questions], protect=lambda q: q["source_type"] == "manual")
        assert [q["id"] for q in unprotected] == ["gen"]   # the old behaviour
        assert [q["id"] for q in protected] == ["gold"]
        assert len(groups) == 1

    def test_two_protected_duplicates_are_both_kept(self):
        questions = [{"id": "a", "text": "Q one", "source_type": "manual"},
                     {"id": "b", "text": "Q one!", "source_type": "manual"}]
        encoder = Mock()
        encoder.encode.return_value = [[1.0, 0.0], [1.0, 0.0]]
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             self._local(encoder):
            val = QuestionValidator(embedding_provider="local")
            kept, _ = val._deduplicate(questions, protect=lambda q: True)
        assert {q["id"] for q in kept} == {"a", "b"}

    def test_the_pipeline_keeps_a_manual_question_over_a_longer_generated_one(self, tmp_path):
        from dokimi.orchestrator.pipeline import EvaluationPipeline

        custom = tmp_path / "custom.json"
        custom.write_text(json.dumps([{"id": "gold", "text": "How do I use parallel_for?"}]))
        pipe = EvaluationPipeline.__new__(EvaluationPipeline)
        pipe.custom_questions_path = custom

        encoder = Mock()
        encoder.encode.return_value = [[0.99, 0.01], [1.0, 0.0]]
        generated = [{"id": "gen", "text": "How exactly do I use parallel_for in my code?"}]
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch('dokimi.questions.validator.OPENAI_AVAILABLE', False), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             self._local(encoder), patch.dict('os.environ', {}, clear=True):
            merged = pipe._merge_questions(generated)
        # Deduplication keeps the manual (longer) question over the shorter generated one
        assert [q["id"] for q in merged] == ["gold"]
        # Verify the manual question's text was kept
        assert merged[0]["question"] == "How do I use parallel_for?"


class TestBackendSelectionEdges:
    def test_a_non_openai_llm_key_does_not_select_openai_embeddings(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator(llm_provider="anthropic", api_key="sk-ant-xxx")
        assert val.embedding_provider == "local"

    def test_an_openai_llm_key_does_select_openai_embeddings(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.OpenAI', Mock(), create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch.dict('os.environ', {}, clear=True):
            val = QuestionValidator(llm_provider="openai", api_key="sk-xxx")
        assert val.embedding_provider == "openai"

    def test_a_non_openai_llm_key_is_not_sent_to_openai_embeddings(self):
        client = Mock()
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.OpenAI', client, create=True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch.dict('os.environ', {"OPENAI_API_KEY": "sk-openai"}, clear=True):
            val = QuestionValidator(llm_provider="anthropic", api_key="sk-ant-xxx")
        assert val.embedding_provider == "openai"
        client.assert_called_once_with(api_key=None)

    def test_an_empty_openai_key_is_no_key(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', False), \
             patch.dict('os.environ', {"OPENAI_API_KEY": ""}, clear=True):
            assert QuestionValidator().embedding_provider == "local"

    def test_an_openai_model_name_on_the_local_backend_fails_at_init(self):
        with patch('dokimi.questions.validator.OPENAI_AVAILABLE', True), \
             patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             patch.dict('os.environ', {}, clear=True):
            with pytest.raises(ValueError, match="is an OpenAI model"):
                QuestionValidator(embedding_model="text-embedding-3-small")

    def test_overriding_the_model_without_a_threshold_warns(self, caplog):
        with patch('dokimi.questions.validator.SENTENCE_TRANSFORMERS_AVAILABLE', True), \
             caplog.at_level("WARNING"):
            val = QuestionValidator(embedding_provider="local",
                                    embedding_model="all-mpnet-base-v2")
        assert "calibrated for" in caplog.text
        assert val.similarity_threshold == EMBEDDING_BACKENDS["local"][1]


class TestLlmKeyRouting:
    """The validator's LLM key is resolved by get_llm, through the real get_llm.

    The litellm wrapper is patched so the test sees the key each provider gets.
    OPENROUTER_API_KEY is only for openrouter.
    """

    @staticmethod
    def _key_for(provider, env, api_key=None):
        with patch('dokimi.questions.validator.LANGCHAIN_AVAILABLE', True), \
             patch('dokimi.llm._GetLLMCompat') as compat, \
             patch.dict('os.environ', env, clear=True):
            QuestionValidator(llm_provider=provider, llm_model="some-model", api_key=api_key)
        compat.assert_called_once()
        return compat.call_args.kwargs["key"]

    @pytest.mark.parametrize(
        "provider", ["google", "google-vertex", "openai-codex", "amazon-bedrock"]
    )
    def test_openrouter_key_is_not_sent_to_another_provider(self, provider):
        key = self._key_for(provider, {"OPENROUTER_API_KEY": "sk-or-secret"})
        assert not key

    def test_google_vertex_uses_gemini_key(self):
        key = self._key_for("google-vertex", {"OPENROUTER_API_KEY": "sk-or-secret",
                                              "GEMINI_API_KEY": "gem-key"})
        assert key == "gem-key"

    def test_openrouter_falls_back_to_its_own_key(self):
        key = self._key_for("openrouter", {"OPENROUTER_API_KEY": "sk-or-secret"})
        assert key == "sk-or-secret"

    @pytest.mark.parametrize("provider", ["openrouter", "google-vertex"])
    def test_an_explicit_key_beats_the_environment(self, provider):
        # Pins `api_key or env` (not `env or api_key`) for openrouter, where
        # both keys are read from the environment, and for the other providers.
        env = {"OPENROUTER_API_KEY": "sk-or-secret", "GEMINI_API_KEY": "gem-key"}
        assert self._key_for(provider, env, api_key="explicit-key") == "explicit-key"
