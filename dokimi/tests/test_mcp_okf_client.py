# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for OKFClient (Google Open Knowledge Format doc source)."""

import pytest

from dokimi.mcp import MCPConnectionError
from dokimi.mcp.okf_client import OKFClient, split_frontmatter


@pytest.fixture
def okf_bundle(tmp_path):
    """Create a small OKF bundle: Markdown files with YAML frontmatter."""
    (tmp_path / "parallel_for.md").write_text(
        "---\n"
        "title: parallel_for\n"
        "type: api\n"
        "tags: [threading, loops]\n"
        "---\n"
        "# parallel_for\n\n"
        "The parallel_for function divides a range into chunks and processes "
        "them in parallel using a thread pool."
    )
    (tmp_path / "runbooks").mkdir()
    (tmp_path / "runbooks" / "install.md").write_text(
        "---\n"
        "title: Installation\n"
        "type: runbook\n"
        "---\n"
        "# Installation\n\n"
        "Run `pip install onetbb` to install the library."
    )
    # A file with no frontmatter — must still be indexed.
    (tmp_path / "notes.md").write_text(
        "# Notes\n\nMiscellaneous notes about task_group and concurrency."
    )
    return tmp_path


# ── split_frontmatter ─────────────────────────────────────────────────────────

def test_split_frontmatter_parses_yaml():
    fm, body = split_frontmatter("---\ntitle: X\ntags: [a, b]\n---\n# Body\n\ntext")
    assert fm == {"title": "X", "tags": ["a", "b"]}
    assert body.startswith("# Body")


def test_split_frontmatter_no_fence():
    fm, body = split_frontmatter("# Just markdown\n\nno frontmatter here")
    assert fm == {}
    assert body.startswith("# Just markdown")


def test_split_frontmatter_malformed_yaml_degrades():
    # Unbalanced brackets -> YAMLError -> empty dict, body preserved.
    fm, body = split_frontmatter("---\ntitle: [unclosed\n---\nbody text")
    assert fm == {}
    assert "body text" in body


def test_split_frontmatter_non_mapping_ignored():
    fm, body = split_frontmatter("---\n- just\n- a\n- list\n---\nbody")
    assert fm == {}
    assert body == "body"


def test_split_frontmatter_body_horizontal_rules_preserved():
    # A document that does NOT open with a fence keeps its mid-body '---'
    # horizontal rules intact — none of the surrounding prose is dropped.
    doc = "# Title\n\nIntro with KEYWORD.\n\n---\n\nSection two."
    fm, body = split_frontmatter(doc)
    assert fm == {}
    assert "Intro with KEYWORD" in body
    assert "Section two" in body
    assert "---" in body


def test_split_frontmatter_value_with_dashes_not_truncated():
    # A frontmatter value containing '---' must not be treated as a fence.
    doc = "---\ntitle: before---after\ndesc: real\n---\nbody text"
    fm, body = split_frontmatter(doc)
    assert fm == {"title": "before---after", "desc": "real"}
    assert body == "body text"


def test_split_frontmatter_open_fence_no_close():
    # Opening fence with no closing fence -> whole text is body.
    doc = "---\ntitle: X\nno closing fence, this is all body"
    fm, body = split_frontmatter(doc)
    assert fm == {}
    assert "no closing fence" in body


def test_split_frontmatter_longer_fences():
    fm, body = split_frontmatter("----\ntitle: X\n----\nbody")
    assert fm == {"title": "X"}
    assert body == "body"


# ── check_connection ──────────────────────────────────────────────────────────

def test_check_connection_existing_bundle(okf_bundle):
    assert OKFClient(okf_bundle).check_connection() is True


def test_check_connection_missing_path(tmp_path):
    assert OKFClient(tmp_path / "nope").check_connection() is False


def test_check_connection_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert OKFClient(empty).check_connection() is False


# ── resolve_library_id ────────────────────────────────────────────────────────

def test_resolve_library_id(okf_bundle):
    assert OKFClient(okf_bundle).resolve_library_id("oneTBB") == f"okf:{okf_bundle}"


# ── get_library_docs ──────────────────────────────────────────────────────────

def test_get_library_docs_returns_results(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "parallel_for thread pool")
    assert len(docs) > 0
    assert all(d["source"] == "okf" for d in docs)
    assert all("content" in d and "relevance_score" in d for d in docs)


@pytest.mark.parametrize("frontmatter", ["title: 2026-99-99", "[" * 100000], ids=["bad-date", "deep"])
def test_get_library_docs_reads_a_card_whose_yaml_does_not_load_as_body_only(okf_bundle, frontmatter):
    (okf_bundle / "broken.md").write_text(f"---\n{frontmatter}\n---\n# Broken\n\nparallel_for broken card.")
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "parallel_for")
    broken = [d for d in docs if "broken card" in d["content"]]
    assert len(broken) == 1 and broken[0]["metadata"].get("title") is None
    assert any(d["metadata"].get("title") == "parallel_for" for d in docs)


def test_get_library_docs_exposes_frontmatter_metadata(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "parallel_for")
    top = docs[0]
    assert top["metadata"].get("title") == "parallel_for"
    assert top["metadata"].get("type") == "api"


def test_get_library_docs_body_excludes_frontmatter(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "parallel_for")
    for d in docs:
        assert "---" not in d["content"].splitlines()[0]
        assert "type: api" not in d["content"]


def test_frontmatter_boosts_relevance(okf_bundle):
    # "runbook" only appears in install.md's frontmatter type field.
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "runbook", max_results=5)
    assert docs[0]["file"].endswith("install.md")


def test_metadata_only_card_is_retrievable(tmp_path):
    # OKF metric/dataset card: all knowledge in frontmatter, empty body.
    (tmp_path / "metric.md").write_text(
        "---\n"
        "title: Daily Active Users\n"
        "type: metric\n"
        "description: DAU counted per calendar day\n"
        "---\n"
    )
    docs = OKFClient(tmp_path).get_library_docs("okf:x", "DAU metric calendar day")
    assert len(docs) == 1
    assert docs[0]["metadata"]["type"] == "metric"
    assert docs[0]["relevance_score"] > 0.0


def test_truly_empty_card_skipped(tmp_path):
    # No frontmatter and no body -> nothing to index.
    (tmp_path / "empty.md").write_text("")
    (tmp_path / "real.md").write_text("---\ntitle: Real\n---\n# Real\n\ncontent here")
    docs = OKFClient(tmp_path).get_library_docs("okf:x", "content")
    assert all(d["file"] != "empty.md" for d in docs)


def test_file_without_frontmatter_indexed(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "task_group concurrency")
    assert any(d["file"] == "notes.md" for d in docs)
    note = next(d for d in docs if d["file"] == "notes.md")
    assert note["metadata"] == {}


def test_get_library_docs_respects_max_results(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "install library", max_results=1)
    assert len(docs) == 1


def test_relevance_score_present(okf_bundle):
    docs = OKFClient(okf_bundle).get_library_docs("okf:oneTBB", "install pip")
    assert all(d["relevance_score"] >= 0.0 for d in docs)


def test_single_file_bundle(tmp_path):
    single = tmp_path / "readme.md"
    single.write_text("---\ntitle: README\n---\n# README\n\nMain documentation file.")
    client = OKFClient(single)
    assert client.check_connection() is True
    docs = client.get_library_docs(f"okf:{single}", "documentation")
    assert len(docs) == 1
    assert docs[0]["metadata"]["title"] == "README"


def test_missing_path_raises(tmp_path):
    client = OKFClient(tmp_path / "missing")
    with pytest.raises(MCPConnectionError):
        client.get_library_docs("okf:missing", "anything")


def test_non_markdown_files_ignored(tmp_path):
    (tmp_path / "doc.md").write_text("---\ntitle: Doc\n---\n# Doc\n\nreal content")
    (tmp_path / "data.json").write_text('{"not": "okf"}')
    (tmp_path / "page.html").write_text("<html>ignored</html>")
    docs = OKFClient(tmp_path).get_library_docs("okf:x", "content")
    assert all(d["file"].endswith(".md") for d in docs)


def test_determinism(okf_bundle):
    client = OKFClient(okf_bundle)
    first = client.get_library_docs("okf:oneTBB", "library install parallel")
    second = client.get_library_docs("okf:oneTBB", "library install parallel")
    assert [d["file"] for d in first] == [d["file"] for d in second]
