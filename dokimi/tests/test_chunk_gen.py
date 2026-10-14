# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ChunkBasedQuestionGenerator."""
import json
from unittest.mock import patch

import pytest


from dokimi.questions.chunk_gen import (
    ChunkBasedQuestionGenerator,
    _chunk_text,
    _strip_html,
    to_question_dicts,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

SAMPLE_HTML = """<html><body>
<h1>oneDAL Installation</h1>
<p>Install oneDAL using pip: <code>pip install onedal</code></p>
<p>The library supports Python 3.8+. GPU support requires SYCL runtime.</p>
<h2>Getting Started</h2>
<p>Import the library and patch scikit-learn:</p>
<code>from sklearnex import patch_sklearn; patch_sklearn()</code>
<p>After patching, all supported estimators use oneDAL acceleration automatically.</p>
</body></html>"""

SAMPLE_TEXT = """
oneDAL Installation

Install oneDAL using pip: pip install onedal

The library supports Python 3.8+. GPU support requires SYCL runtime.

Getting Started

Import the library and patch scikit-learn:
from sklearnex import patch_sklearn; patch_sklearn()

After patching, all supported estimators use oneDAL acceleration automatically.
"""


def _mock_llm(questions):
    return patch(
        "dokimi.questions.chunk_gen.llm_call",
        return_value=json.dumps(questions),
    )


def _mock_fetch(content):
    return patch(
        "dokimi.questions.chunk_gen._fetch_url",
        return_value=content,
    )


# ── _strip_html ───────────────────────────────────────────────────────────────

def test_strip_html_removes_tags():
    result = _strip_html(SAMPLE_HTML)
    assert "<h1>" not in result
    assert "oneDAL Installation" in result


def test_strip_html_removes_script():
    html = "<html><script>alert('x')</script><p>Hello</p></html>"
    result = _strip_html(html)
    assert "alert" not in result
    assert "Hello" in result


@pytest.mark.parametrize("end_tag", ["</script >", "</SCRIPT>", "</script\n>"])
def test_strip_html_removes_script_whatever_its_end_tag_looks_like(end_tag):
    # Browsers close a script on all of these; a `</script>` regex leaked the body.
    result = _strip_html(f"<p>Hello</p><script>alert('x'){end_tag}<p>World</p>")
    assert "alert" not in result
    assert "Hello" in result and "World" in result


def test_strip_html_keeps_text_that_only_looks_like_a_tag():
    assert _strip_html("<p>if a < b and c > d</p>") == "if a < b and c > d"


# ── _chunk_text ───────────────────────────────────────────────────────────────

def test_chunk_text_basic():
    chunks = _chunk_text(SAMPLE_TEXT, max_chars=300)
    assert len(chunks) >= 1
    for c in chunks:
        assert len(c) >= 1


def test_chunk_text_respects_max_chars():
    long_text = "\n\n".join(["word " * 100] * 10)
    chunks = _chunk_text(long_text, max_chars=500)
    # Each chunk should be under max + some overlap
    for c in chunks:
        assert len(c) <= 700  # generous bound due to overlap


def test_chunk_text_drops_short_paragraphs():
    text = "a\n\n\n\nReal content paragraph with enough text to be useful here.\n\nb\n\n"
    chunks = _chunk_text(text)
    # Short single chars should be dropped
    for c in chunks:
        assert len(c) >= 30


# ── ChunkBasedQuestionGenerator ───────────────────────────────────────────────

def test_generate_from_html():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=2, min_chunk_chars=50)
    with _mock_fetch(SAMPLE_HTML), _mock_llm(["How to install oneDAL?", "What Python version is needed?"]):
        result = gen.generate("oneDAL", "https://example.com", total_questions=4)
    assert len(result.questions) > 0
    assert result.questions[0].question_source == "chunk"
    assert result.source_url == "https://example.com"


def test_generate_from_plain_text():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=1, min_chunk_chars=50)
    with _mock_fetch(SAMPLE_TEXT), _mock_llm(["What does patch_sklearn do?"]):
        result = gen.generate("oneDAL", "https://example.com", total_questions=2)
    assert len(result.questions) >= 1
    assert all(isinstance(q.question, str) for q in result.questions)


def test_generate_respects_total_questions():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=2, min_chunk_chars=50)
    questions_returned = ["Q1?", "Q2?"]
    with _mock_fetch(SAMPLE_TEXT * 5), _mock_llm(questions_returned):
        result = gen.generate("oneDAL", "https://x.com", total_questions=3)
    assert len(result.questions) <= 3


def test_generate_handles_fetch_error():
    gen = ChunkBasedQuestionGenerator()
    with patch("dokimi.questions.chunk_gen._fetch_url", side_effect=Exception("timeout")):
        result = gen.generate("oneDAL", "https://bad-url.com", total_questions=4)
    assert result.questions == []
    assert result.total_chunks == 0


def test_generate_handles_llm_error_gracefully():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=1, min_chunk_chars=50)
    with _mock_fetch(SAMPLE_TEXT), \
         patch("dokimi.questions.chunk_gen.llm_call", side_effect=RuntimeError("llm down")):
        result = gen.generate("oneDAL", "https://x.com", total_questions=2)
    # Should return empty, not crash
    assert result.questions == []


def test_chunk_question_has_ground_truth():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=1, min_chunk_chars=50)
    with _mock_fetch(SAMPLE_TEXT), _mock_llm(["How to install?"]):
        result = gen.generate("oneDAL", "https://x.com", total_questions=1)
    if result.questions:
        assert result.questions[0].chunk_text != ""
        assert result.questions[0].chunk_index >= 0


# ── to_question_dicts ─────────────────────────────────────────────────────────

def test_to_question_dicts_format():
    gen = ChunkBasedQuestionGenerator(questions_per_chunk=1, min_chunk_chars=50)
    with _mock_fetch(SAMPLE_TEXT), _mock_llm(["Test question?"]):
        result = gen.generate("oneDAL", "https://x.com", total_questions=1)
    if result.questions:
        dicts = to_question_dicts(result)
        assert dicts[0]["question"] == "Test question?"
        assert "ground_truth_chunk" in dicts[0]
        assert "chunk_index" in dicts[0]
        assert dicts[0]["question_source"] == "chunk"
