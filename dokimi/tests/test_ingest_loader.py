# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ingest/loader.py document discovery."""

from __future__ import annotations

from dokimi.ingest.loader import discover_markdown


def _touch(root, *parts):
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# doc\n", encoding="utf-8")
    return path


def test_discover_markdown_returns_sorted_docs(tmp_path):
    b = _touch(tmp_path, "guide", "b.md")
    a = _touch(tmp_path, "a.md")
    _touch(tmp_path, "notes.txt")
    assert discover_markdown(tmp_path) == sorted([a, b])


def test_discover_markdown_skips_vendored_and_vcs_dirs(tmp_path):
    kept = _touch(tmp_path, "guide", "intro.md")
    for skipped in ("node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages"):
        _touch(tmp_path, skipped, "pkg", "README.md")
    _touch(tmp_path, "guide", "node_modules", "dep", "CHANGELOG.md")
    assert discover_markdown(tmp_path) == [kept]


def test_discover_markdown_keeps_docs_that_merely_resemble_skipped_names(tmp_path):
    kept = _touch(tmp_path, "node_modules_guide", "setup.md")
    assert discover_markdown(tmp_path) == [kept]


def test_discover_markdown_root_inside_a_skipped_dir_name_is_still_searched(tmp_path):
    a = _touch(tmp_path, "venv", "proj", "docs", "a.md")
    assert discover_markdown(tmp_path / "venv" / "proj" / "docs") == [a]
