# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for RagasSeedExtractor."""

import json
import pytest
from unittest.mock import Mock

from dokimi.questions.ragas_seed import RagasSeedExtractor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_mcp_client():
    """Minimal MCP client returning fake docs."""
    client = Mock()
    client.get_library_docs.return_value = [
        {
            "content": (
                "# oneTBB API\n\n"
                "Use `tbb::parallel_for` for data parallelism.\n"
                "Use `tbb::task_arena` to limit concurrency.\n"
                "The `tbb::flow::graph` enables pipeline patterns.\n"
                "See also `tbb::blocked_range` and `tbb::concurrent_vector`.\n"
                "tbb::mutex provides mutual exclusion.\n"
            ),
            "source": "context7",
            "library_id": "uxlfoundation/oneTBB",
        }
    ]
    return client


@pytest.fixture
def mock_llm():
    """LLM that returns a valid JSON topic list."""
    llm = Mock()
    resp = Mock()
    resp.content = json.dumps([
        "parallel_for", "task_arena", "flow_graph",
        "blocked_range", "concurrent_vector",
    ])
    llm.invoke.return_value = resp
    return llm


@pytest.fixture
def extractor(mock_mcp_client, mock_llm):
    """RagasSeedExtractor with mocked client and LLM."""
    return RagasSeedExtractor(mcp_client=mock_mcp_client, llm=mock_llm)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestRagasSeedExtractorInit:
    def test_defaults(self):
        e = RagasSeedExtractor()
        assert e.mcp_client is None
        assert e.llm is None
        assert e.cache_dir is None

    def test_custom(self, tmp_path, mock_mcp_client, mock_llm):
        cache = tmp_path / "cache"
        e = RagasSeedExtractor(
            mcp_client=mock_mcp_client,
            llm=mock_llm,
            cache_dir=cache,
        )
        assert e.mcp_client is mock_mcp_client
        assert e.llm is mock_llm
        assert e.cache_dir == cache
        assert cache.exists()


# ---------------------------------------------------------------------------
# extract_topics
# ---------------------------------------------------------------------------

class TestExtractTopics:
    def test_returns_the_llm_topics_in_order(self, extractor):
        topics = extractor.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        assert topics == ["parallel_for", "task_arena", "flow_graph",
                          "blocked_range", "concurrent_vector"]

    def test_respects_max_topics(self, extractor):
        topics = extractor.extract_topics("uxlfoundation/oneTBB", "oneTBB", max_topics=3)
        assert len(topics) <= 3

    def test_llm_topics_returned(self, extractor):
        topics = extractor.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        assert "parallel_for" in topics
        assert "task_arena" in topics

    def test_caching_prevents_second_llm_call(self, tmp_path, mock_mcp_client, mock_llm):
        cache = tmp_path / "cache"
        e = RagasSeedExtractor(mcp_client=mock_mcp_client, llm=mock_llm, cache_dir=cache)

        e.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        e.extract_topics("uxlfoundation/oneTBB", "oneTBB")  # second call

        # MCP and LLM called only once (second is from cache)
        assert mock_mcp_client.get_library_docs.call_count == 1
        assert mock_llm.invoke.call_count == 1

    def test_cache_written_to_disk(self, tmp_path, mock_mcp_client, mock_llm):
        cache = tmp_path / "cache"
        e = RagasSeedExtractor(mcp_client=mock_mcp_client, llm=mock_llm, cache_dir=cache)
        e.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        json_files = list(cache.glob("topics_*.json"))
        assert len(json_files) == 1

    def test_no_mcp_client_returns_fallback(self, mock_llm):
        e = RagasSeedExtractor(llm=mock_llm)
        topics = e.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        # With no MCP, docs are empty: the generic fallback, without asking the LLM
        assert topics == RagasSeedExtractor._fallback_topics("oneTBB")
        mock_llm.invoke.assert_not_called()

    def test_mcp_error_returns_fallback(self, mock_llm):
        bad_client = Mock()
        bad_client.get_library_docs.side_effect = Exception("Network error")
        e = RagasSeedExtractor(mcp_client=bad_client, llm=mock_llm)
        topics = e.extract_topics("uxlfoundation/oneTBB", "oneTBB")
        assert topics == RagasSeedExtractor._fallback_topics("oneTBB")
        mock_llm.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_via_llm
# ---------------------------------------------------------------------------

class TestExtractViaLLM:
    def test_parses_json_array(self, mock_llm):
        e = RagasSeedExtractor(llm=mock_llm)
        topics = e._extract_via_llm("some docs", "oneTBB", max_topics=10)
        assert "parallel_for" in topics

    def test_limits_to_max_topics(self, mock_llm):
        mock_llm.invoke.return_value.content = json.dumps(
            [f"topic_{i}" for i in range(30)]
        )
        e = RagasSeedExtractor(llm=mock_llm)
        topics = e._extract_via_llm("docs", "oneTBB", max_topics=5)
        assert len(topics) <= 5

    def test_deduplicates(self, mock_llm):
        mock_llm.invoke.return_value.content = json.dumps(
            ["parallel_for", "parallel_for", "task_arena"]
        )
        e = RagasSeedExtractor(llm=mock_llm)
        topics = e._extract_via_llm("docs", "oneTBB", max_topics=10)
        assert topics.count("parallel_for") == 1

    def test_invalid_json_falls_back_to_heuristic(self, mock_llm):
        mock_llm.invoke.return_value.content = "Not JSON at all"
        e = RagasSeedExtractor(llm=mock_llm)
        # Should not raise; falls back to heuristic (the :: name and its code span, deduplicated)
        topics = e._extract_via_llm("Use `tbb::parallel_for` here.", "oneTBB", max_topics=10)
        assert topics == ["tbb::parallel_for"]

    def test_no_llm_uses_heuristic(self):
        e = RagasSeedExtractor()
        topics = e._extract_via_llm(
            "Use `tbb::parallel_for` and `tbb::task_arena`.", "oneTBB", max_topics=10
        )
        assert topics == ["tbb::parallel_for", "tbb::task_arena"]


# ---------------------------------------------------------------------------
# _extract_heuristic
# ---------------------------------------------------------------------------

class TestExtractHeuristic:
    def test_finds_namespaced_identifiers(self):
        docs = "tbb::parallel_for is fast. tbb::mutex is safe."
        topics = RagasSeedExtractor._extract_heuristic(docs, "oneTBB", max_topics=20)
        assert "tbb::parallel_for" in topics
        assert "tbb::mutex" in topics

    def test_finds_backtick_terms(self):
        docs = "Use `parallel_for` for loops and `task_arena` for isolation."
        topics = RagasSeedExtractor._extract_heuristic(docs, "oneTBB", max_topics=20)
        assert "parallel_for" in topics
        assert "task_arena" in topics

    def test_respects_max_topics(self):
        docs = " ".join(f"`topic_{i}`" for i in range(50))
        topics = RagasSeedExtractor._extract_heuristic(docs, "oneTBB", max_topics=5)
        assert len(topics) <= 5

    def test_deduplicates(self):
        docs = "`parallel_for` and `parallel_for` again."
        topics = RagasSeedExtractor._extract_heuristic(docs, "oneTBB", max_topics=20)
        assert topics.count("parallel_for") == 1

    def test_empty_docs_returns_fallback(self):
        topics = RagasSeedExtractor._extract_heuristic("", "oneTBB", max_topics=10)
        assert topics == RagasSeedExtractor._fallback_topics("oneTBB")
        assert "installation" in topics


# ---------------------------------------------------------------------------
# _fallback_topics
# ---------------------------------------------------------------------------

class TestFallbackTopics:
    def test_generic_fallback(self):
        topics = RagasSeedExtractor._fallback_topics("unknownLib")
        assert len(topics) > 0
        assert "installation" in topics

    @pytest.mark.parametrize("library_name", ["oneTBB", "oneDAL", "unknownLib", ""])
    def test_the_fallback_is_the_same_for_every_library(self, library_name):
        """No product gets a hand-written topic list; supply one with --topics."""
        assert RagasSeedExtractor._fallback_topics(library_name) == (
            RagasSeedExtractor._fallback_topics("unknownLib")
        )


# ---------------------------------------------------------------------------
# _fetch_docs
# ---------------------------------------------------------------------------

class TestFetchDocs:
    def test_calls_mcp_client(self, mock_mcp_client):
        e = RagasSeedExtractor(mcp_client=mock_mcp_client)
        result = e._fetch_docs("uxlfoundation/oneTBB", "oneTBB", max_tokens=1000)
        assert isinstance(result, str)
        assert len(result) > 0
        mock_mcp_client.get_library_docs.assert_called_once()

    def test_no_client_returns_empty(self):
        e = RagasSeedExtractor()
        result = e._fetch_docs("uxlfoundation/oneTBB", "oneTBB", max_tokens=1000)
        assert result == ""

    def test_client_error_returns_empty(self):
        bad = Mock()
        bad.get_library_docs.side_effect = RuntimeError("oops")
        e = RagasSeedExtractor(mcp_client=bad)
        result = e._fetch_docs("uxlfoundation/oneTBB", "oneTBB", max_tokens=1000)
        assert result == ""

    def test_merges_multiple_chunks(self, mock_mcp_client):
        mock_mcp_client.get_library_docs.return_value = [
            {"content": "Chunk A"},
            {"content": "Chunk B"},
        ]
        e = RagasSeedExtractor(mcp_client=mock_mcp_client)
        result = e._fetch_docs("lib/id", "lib", max_tokens=1000)
        assert "Chunk A" in result
        assert "Chunk B" in result
