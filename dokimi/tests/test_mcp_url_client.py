# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for URLClient."""

import pytest
from unittest.mock import patch, MagicMock

import httpx

from dokimi.mcp.url_client import URLClient
from dokimi.mcp import MCPConnectionError


SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>oneTBB Docs</title></head>
<body>
<h1>parallel_for</h1>
<p>The parallel_for function splits work across threads using a thread pool.</p>

<h2>Example</h2>
<p>Use parallel_for to parallelise loop iterations efficiently.
Each iteration runs independently on available threads.</p>

<h2>task_group</h2>
<p>A task_group spawns independent tasks that run concurrently.
Tasks can be waited on with group.wait().</p>
</body></html>"""

SAMPLE_PLAIN = (
    "parallel_for divides a range into chunks and runs them in parallel.\n\n"
    "task_group lets you spawn multiple tasks and wait for completion.\n\n"
    "Install oneTBB via pip or cmake."
)


def _mock_response(text: str, status_code: int = 200, content_type: str = "text/html"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


# ── check_connection ──────────────────────────────────────────────────────────

def test_check_connection_success():
    client = URLClient("https://example.com/docs")
    with patch("httpx.head", return_value=_mock_response("", 200)) as mock_head:
        assert client.check_connection() is True
        mock_head.assert_called_once()


def test_check_connection_404():
    client = URLClient("https://example.com/missing")
    resp = _mock_response("", 404)
    with patch("httpx.head", return_value=resp):
        assert client.check_connection() is False


def test_check_connection_network_error():
    client = URLClient("https://unreachable.example.com")
    with patch("httpx.head", side_effect=httpx.ConnectError("no route")):
        assert client.check_connection() is False


# ── resolve_library_id ────────────────────────────────────────────────────────

def test_resolve_library_id():
    url = "https://example.com/api"
    client = URLClient(url)
    assert client.resolve_library_id("anything") == url


# ── get_library_docs — HTML ───────────────────────────────────────────────────

def test_get_library_docs_strips_html():
    client = URLClient("https://example.com/docs")
    with patch("httpx.get", return_value=_mock_response(SAMPLE_HTML)):
        docs = client.get_library_docs("https://example.com/docs", "parallel_for")
    assert len(docs) > 0
    for d in docs:
        assert "<html>" not in d["content"]
        assert "<p>" not in d["content"]


def test_get_library_docs_relevance_ordering():
    client = URLClient("https://example.com/docs")
    with patch("httpx.get", return_value=_mock_response(SAMPLE_PLAIN, content_type="text/plain")):
        docs = client.get_library_docs("https://example.com/docs", "parallel_for range chunks", max_results=3)
    assert len(docs) > 0
    # Top result should mention parallel_for
    assert "parallel_for" in docs[0]["content"].lower()


def test_get_library_docs_respects_max_results():
    client = URLClient("https://example.com/docs")
    with patch("httpx.get", return_value=_mock_response(SAMPLE_PLAIN, content_type="text/plain")):
        docs = client.get_library_docs("https://example.com/docs", "task", max_results=1)
    assert len(docs) <= 1


def test_get_library_docs_metadata():
    url = "https://example.com/docs"
    client = URLClient(url)
    with patch("httpx.get", return_value=_mock_response(SAMPLE_HTML)):
        docs = client.get_library_docs(url, "parallel_for")
    for d in docs:
        assert d["source"] == "url"
        assert d["url"] == url
        assert "relevance_score" in d
        assert 0.0 <= d["relevance_score"] <= 1.0


# ── get_library_docs — errors ─────────────────────────────────────────────────

def test_get_library_docs_http_error_raises():
    client = URLClient("https://example.com/docs")
    err_resp = MagicMock(spec=httpx.Response)
    err_resp.status_code = 503
    with patch("httpx.get", side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=err_resp)):
        with pytest.raises(MCPConnectionError):
            client.get_library_docs("https://example.com/docs", "parallel")


def test_get_library_docs_timeout_raises():
    client = URLClient("https://example.com/docs", timeout=1)
    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(MCPConnectionError):
            client.get_library_docs("https://example.com/docs", "parallel")


# ── caching ───────────────────────────────────────────────────────────────────

def test_caching_skips_second_fetch(tmp_path):
    url = "https://example.com/docs"
    client = URLClient(url, cache_dir=tmp_path)
    with patch("httpx.get", return_value=_mock_response(SAMPLE_PLAIN, content_type="text/plain")) as mock_get:
        first = client.get_library_docs(url, "parallel")
        second = client.get_library_docs(url, "parallel")
    # Second call should use cache, not hit network, and return the same docs
    assert mock_get.call_count == 1
    assert second == first and "parallel_for" in first[0]["content"]
