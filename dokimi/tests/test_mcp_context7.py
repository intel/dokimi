# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for Context7 MCP client."""

import os

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from dokimi.mcp.context7 import Context7Client, create_context7_client
from dokimi.mcp import MCPConnectionError, MCPLibraryNotFoundError


class TestContext7Client:
    """Test suite for Context7Client."""
    
    def test_init_defaults(self):
        """Test client initialization with defaults."""
        client = Context7Client()
        assert client.endpoint == "https://context7.com"
        assert client.timeout == 30
        assert client.max_retries == 3
        assert client.cache_dir is None
    
    def test_init_custom(self, tmp_path):
        """Test client initialization with custom parameters."""
        cache_dir = tmp_path / "cache"
        client = Context7Client(
            endpoint="https://custom.example.com",
            cache_dir=cache_dir,
            timeout=60,
            max_retries=5
        )
        assert client.endpoint == "https://custom.example.com"
        assert client.cache_dir == cache_dir
        assert client.timeout == 60
        assert client.max_retries == 5
        assert cache_dir.exists()
    
    @pytest.mark.parametrize(
        "qualified_id",
        [
            "someorg/somerepo",           # GitHub repo form
            "example_com_docs_widget",    # docs-website form
        ],
    )
    def test_resolve_library_id_already_qualified(self, qualified_id):
        """A qualified ID of either form is returned as-is."""
        client = Context7Client()
        assert client.resolve_library_id(qualified_id) == qualified_id

    def test_resolve_library_id_rejects_an_unqualified_name(self):
        """An unqualified name is an error, not a guessed org/name ID."""
        client = Context7Client()
        with pytest.raises(ValueError) as exc_info:
            client.resolve_library_id("somelibrary")
        message = str(exc_info.value)
        # The message must name both ways of supplying the real ID.
        assert "context7_id" in message
        assert "--context7-id" in message
        assert "products.yaml" in message

    @patch('httpx.get')
    def test_get_library_docs_success(self, mock_get):
        """Test successful context retrieval."""
        # Mock HTTP response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# oneTBB Documentation\n\nParallel algorithms..."
        mock_get.return_value = mock_response
        
        client = Context7Client()
        docs = client.get_library_docs("uxlfoundation/oneTBB", "parallel_for", max_tokens=1000)
        
        assert len(docs) == 1
        assert docs[0]["content"] == mock_response.text
        assert docs[0]["source"] == "context7"
        assert docs[0]["library_id"] == "uxlfoundation/oneTBB"
        assert docs[0]["query"] == "parallel_for"
        assert not docs[0]["cached"]
        
        # Verify URL construction
        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert "uxlfoundation/oneTBB/llms.txt" in called_url
        assert "tokens=1000" in called_url
        assert "topic=parallel_for" in called_url
    
    @patch('httpx.get')
    def test_get_library_docs_with_cache(self, mock_get, tmp_path):
        """Test context retrieval with caching."""
        cache_dir = tmp_path / "cache"
        
        # Mock HTTP response, long enough not to be an error stub
        content = "Cached content about parallel_for. " * 4
        mock_response = Mock()
        mock_response.text = content
        mock_get.return_value = mock_response
        
        client = Context7Client(cache_dir=cache_dir)
        
        # First call - should fetch and cache
        docs1 = client.get_library_docs("uxlfoundation/oneTBB", "test_query", max_tokens=500)
        assert not docs1[0]["cached"]
        assert mock_get.call_count == 1
        
        # Second call - should use cache
        docs2 = client.get_library_docs("uxlfoundation/oneTBB", "test_query", max_tokens=500)
        assert docs2[0]["cached"]
        assert docs2[0]["content"] == content
        assert mock_get.call_count == 1  # No additional HTTP call
    
    @patch('httpx.get')
    def test_get_library_docs_404(self, mock_get):
        """Test handling of library not found (404)."""
        import httpx
        
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.side_effect = httpx.HTTPStatusError(
            "Not found",
            request=Mock(),
            response=mock_response
        )
        
        client = Context7Client()
        
        with pytest.raises(MCPLibraryNotFoundError) as exc_info:
            client.get_library_docs("uxlfoundation/nonexistent", "query")
        
        assert "not found" in str(exc_info.value).lower()
    
    @patch('httpx.get')
    def test_get_library_docs_timeout(self, mock_get):
        """Test handling of request timeout."""
        import httpx
        
        mock_get.side_effect = httpx.TimeoutException("Timeout")
        
        client = Context7Client(timeout=10)
        
        with pytest.raises(MCPConnectionError) as exc_info:
            client.get_library_docs("uxlfoundation/oneTBB", "query")
        
        assert "timeout" in str(exc_info.value).lower()
    
    @patch('httpx.get')
    def test_get_library_docs_html_response(self, mock_get):
        """Test detection of HTML error responses (wrapped as MCPConnectionError)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<!DOCTYPE html><html>Error page</html>"
        mock_get.return_value = mock_response
        
        client = Context7Client()
        
        # HTML responses are detected as MCPLibraryNotFoundError internally,
        # but the outer except re-raises as MCPConnectionError
        with pytest.raises((MCPLibraryNotFoundError, MCPConnectionError)):
            client.get_library_docs("uxlfoundation/oneTBB", "query")
    
    @patch.object(Context7Client, 'get_library_docs')
    def test_check_connection_probes_the_library_it_was_given(self, mock_get_docs):
        """The probe target is the caller's, not a hardcoded library."""
        mock_get_docs.return_value = [{"content": "Test content"}]

        client = Context7Client()
        assert client.check_connection("someorg/somerepo", "widgets") is True

        mock_get_docs.assert_called_once()
        args, kwargs = mock_get_docs.call_args
        assert args[0] == "someorg/somerepo"
        assert args[1] == "widgets"

    @patch.object(Context7Client, 'get_library_docs')
    def test_check_connection_failure(self, mock_get_docs):
        """Test failed connection check."""
        mock_get_docs.side_effect = MCPConnectionError("Connection failed")

        client = Context7Client()
        assert client.check_connection("someorg/somerepo") is False

    def test_create_context7_client_default(self):
        """Test factory function with default cache dir."""
        client = create_context7_client()
        assert client.cache_dir == Path(".cache") / "context7"
    
    def test_create_context7_client_custom(self, tmp_path):
        """Test factory function with custom cache dir."""
        cache_dir = tmp_path / "custom_cache"
        client = create_context7_client(cache_dir=cache_dir)
        assert client.cache_dir == cache_dir


class TestContext7Integration:
    """Integration tests (require network or full mocks)."""
    
    @pytest.mark.skipif(
        True,  # Skip by default, enable for manual testing
        reason="Integration test - requires network and valid Context7 library"
    )
    def test_real_context7_call(self):
        """Test real Context7 API call (manual test only).

        Set the library id and topic to something the Context7 instance you
        are pointing at really serves; there is no default library, because a
        guessed id is exactly what this client refuses to produce.
        """
        library_id = os.environ.get("CONTEXT7_PROBE_ID", "")
        query = os.environ.get("CONTEXT7_PROBE_QUERY", "getting started")
        assert library_id, "set CONTEXT7_PROBE_ID to a qualified Context7 library id"

        client = create_context7_client()
        docs = client.get_library_docs(library_id, query, max_tokens=500)

        assert len(docs) == 1
        assert len(docs[0]["content"]) > 0
        assert client.check_connection(library_id, query) is True
