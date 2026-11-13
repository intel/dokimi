# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What the doc and topic caches store and replay: URLClient, Context7Client, RagasSeedExtractor.

A cache entry that should not have been stored (a truncated page, an error stub, the generic
fallback topics) is never replayed, and an unreadable entry is a miss with a warning naming it.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from dokimi.mcp.context7 import Context7Client
from dokimi.mcp.url_client import URLClient
from dokimi.questions.ragas_seed import RagasSeedExtractor

URL = "https://example.com/spec.txt"
# Paragraphs of 100 characters each, so a cut at any size limit lands mid-page.
PAGE = "\n\n".join(f"{i:03d} " + "x" * 96 for i in range(40))
DOCS = "parallel_for splits a range into chunks. " * 5  # a Context7 answer over the stub limit
LIB, QUERY = "uxlfoundation/oneTBB", "parallel_for"


def _response(text: str, content_type: str = "text/plain") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = text
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


def _only_entry(cache_dir):
    entries = [p for p in cache_dir.iterdir() if not p.name.startswith(".")]
    assert len(entries) == 1, entries
    return entries[0]


def _url_text(client: URLClient) -> str:
    return "\n\n".join(d["content"] for d in client.get_library_docs(URL, "x", max_results=100,
                                                                     max_tokens=100_000))


# ── URLClient ────────────────────────────────────────────────────────────────

def test_url_page_cut_at_a_smaller_limit_is_not_replayed_to_a_larger_one(tmp_path):
    with patch("httpx.get", return_value=_response(PAGE)) as get:
        cut = _url_text(URLClient(URL, cache_dir=tmp_path, max_page_size_kchars=1))
        whole = _url_text(URLClient(URL, cache_dir=tmp_path, max_page_size_kchars=8))
    assert len(cut) < 1100 and len(whole) == len(PAGE)
    assert get.call_count == 2


def test_url_page_cached_under_a_larger_limit_is_not_replayed_past_a_smaller_one(tmp_path):
    with patch("httpx.get", return_value=_response(PAGE)) as get:
        _url_text(URLClient(URL, cache_dir=tmp_path, max_page_size_kchars=8))
        cut = _url_text(URLClient(URL, cache_dir=tmp_path, max_page_size_kchars=1))
    assert len(cut) < 1100
    assert get.call_count == 2


def test_url_page_with_no_text_is_not_stored(tmp_path):
    client = URLClient(URL, cache_dir=tmp_path)
    with patch("httpx.get", return_value=_response("<html><body> </body></html>", "text/html")) as get:
        assert client.get_library_docs(URL, "x") == []
        assert client.get_library_docs(URL, "x") == []
    assert get.call_count == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stored", [b"\xff\xfe not utf-8", b"  \n"])
def test_url_bad_entry_is_a_miss_and_is_overwritten(tmp_path, caplog, stored):
    with patch("httpx.get", return_value=_response(PAGE)):
        _url_text(URLClient(URL, cache_dir=tmp_path))
    entry = _only_entry(tmp_path)
    entry.write_bytes(stored)
    with patch("httpx.get", return_value=_response(PAGE)) as get, caplog.at_level(logging.WARNING):
        text = _url_text(URLClient(URL, cache_dir=tmp_path))
    assert get.call_count == 1 and len(text) == len(PAGE)
    assert entry.read_text(encoding="utf-8") == PAGE
    assert str(entry) in caplog.text


# ── Context7Client ───────────────────────────────────────────────────────────

def test_context7_short_answer_is_returned_but_not_stored(tmp_path):
    client = Context7Client(cache_dir=tmp_path)
    with patch("httpx.get", return_value=_response("No content found.")) as get:
        first = client.get_library_docs(LIB, QUERY)
        second = client.get_library_docs(LIB, QUERY)
    assert first[0]["content"] == "No content found."
    assert second[0]["cached"] is False
    assert get.call_count == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stored", [b"Error: rate limited", b"\xff\xfe not utf-8"])
def test_context7_bad_entry_is_a_miss_and_is_overwritten(tmp_path, caplog, stored):
    with patch("httpx.get", return_value=_response(DOCS)):
        Context7Client(cache_dir=tmp_path).get_library_docs(LIB, QUERY)
    entry = _only_entry(tmp_path)
    entry.write_bytes(stored)
    with patch("httpx.get", return_value=_response(DOCS)) as get, caplog.at_level(logging.WARNING):
        docs = Context7Client(cache_dir=tmp_path).get_library_docs(LIB, QUERY)
    assert get.call_count == 1
    assert docs[0]["content"] == DOCS and docs[0]["cached"] is False
    assert entry.read_text(encoding="utf-8") == DOCS
    assert str(entry) in caplog.text


# ── RagasSeedExtractor ───────────────────────────────────────────────────────

def _extractor(cache_dir, docs: str = "Use `tbb::parallel_for` and `task_arena`.") -> RagasSeedExtractor:
    client = MagicMock()
    client.get_library_docs.return_value = [{"content": docs}]
    return RagasSeedExtractor(mcp_client=client, cache_dir=cache_dir)


@pytest.mark.parametrize("stored", [
    '["tbb::parallel_for", "task_ar',  # cut mid-write
    '{"topics": ["parallel_for"]}',
    "[]",
    '["parallel_for", 3]',
    json.dumps(RagasSeedExtractor._fallback_topics("oneTBB")),
])
def test_topics_bad_entry_is_a_miss_and_is_overwritten(tmp_path, caplog, stored):
    _extractor(tmp_path).extract_topics(LIB, "oneTBB")
    entry = _only_entry(tmp_path)
    entry.write_text(stored, encoding="utf-8")
    extractor = _extractor(tmp_path)
    with caplog.at_level(logging.WARNING):
        topics = extractor.extract_topics(LIB, "oneTBB")
    assert topics == ["tbb::parallel_for", "task_arena"]
    assert extractor.mcp_client.get_library_docs.call_count == 1
    assert json.loads(entry.read_text(encoding="utf-8")) == topics
    assert str(entry) in caplog.text


def test_topics_fallback_is_returned_but_not_stored(tmp_path):
    extractor = _extractor(tmp_path, docs="Error: library not found")
    first = extractor.extract_topics(LIB, "oneTBB")
    second = extractor.extract_topics(LIB, "oneTBB")
    assert first == second == RagasSeedExtractor._fallback_topics("oneTBB")
    assert extractor.mcp_client.get_library_docs.call_count == 2
    assert list(tmp_path.iterdir()) == []


def test_topics_empty_llm_answer_is_not_stored(tmp_path):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="[]")
    extractor = _extractor(tmp_path)
    extractor.llm = llm
    assert extractor.extract_topics(LIB, "oneTBB") == []
    assert extractor.extract_topics(LIB, "oneTBB") == []
    assert llm.invoke.call_count == 2
    assert list(tmp_path.iterdir()) == []


def test_topics_blank_llm_entry_is_dropped_and_the_rest_is_stored(tmp_path):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content='["parallel_for", " ", "task_arena"]')
    extractor = _extractor(tmp_path)
    extractor.llm = llm
    assert extractor.extract_topics(LIB, "oneTBB") == ["parallel_for", "task_arena"]
    assert extractor.extract_topics(LIB, "oneTBB") == ["parallel_for", "task_arena"]
    assert llm.invoke.call_count == 1


# ── the shared write ─────────────────────────────────────────────────────────

def test_failed_cache_write_keeps_the_old_entry_and_leaves_no_tmp(tmp_path, caplog):
    from dokimi.mcp.utils import write_cache_text

    entry = tmp_path / "k.txt"
    entry.write_text("old", encoding="utf-8")
    with patch("dokimi.mcp.utils.os.replace", side_effect=OSError("disk full")), \
            caplog.at_level(logging.WARNING):
        write_cache_text(entry, "new")
    assert entry.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["k.txt"]
    assert str(entry) in caplog.text and "disk full" in caplog.text


def test_failed_cache_write_does_not_fail_the_fetch(tmp_path, caplog):
    with patch("httpx.get", return_value=_response(PAGE)), \
            patch("dokimi.mcp.utils.os.replace", side_effect=OSError("disk full")), \
            caplog.at_level(logging.WARNING):
        text = _url_text(URLClient(URL, cache_dir=tmp_path))
    assert len(text) == len(PAGE)
    assert list(tmp_path.iterdir()) == [] and "disk full" in caplog.text


# ── eval questions generate ──────────────────────────────────────────────────

def test_generate_does_not_replay_one_doc_source_topics_for_another(tmp_path, monkeypatch):
    import argparse
    from dokimi.commands import questions as cmd
    from dokimi.mcp import factory
    import dokimi.questions as questions_pkg

    docs = {"context7": "Use `tbb::parallel_for` here.", "local:/docs": "Use `tbb::flow_graph` here."}
    seen = []

    class _Generator:
        def __init__(self, **_):
            pass

        def generate_questions(self, topics, **_):
            seen.append(topics)
            return []

        def save_questions(self, *a, **kw):
            pass

    def _client(source):
        client = MagicMock()
        client.get_library_docs.return_value = [{"content": docs[source]}]
        return client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(factory, "create_doc_source_client", _client)
    monkeypatch.setattr(factory, "resolve_library_id_for_product", lambda *a, **kw: LIB)
    monkeypatch.setattr(questions_pkg, "QuestionGenerator", _Generator)
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps({"personas": [{"id": "dev", "name": "dev", "skill_level": "expert"}]}), encoding="utf-8")
    for source in docs:
        cmd.cmd_questions_generate(argparse.Namespace(
            product="oneTBB", personas=str(personas), topics=None, doc_source=source, context7_id=None,
            top_k=3, provider="openai", model="m", count=1, validate=False, output=None))
    assert seen == [["tbb::parallel_for"], ["tbb::flow_graph"]]
