# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the optional Docling ingestion loader (graceful degradation)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dokimi.ingest import docling_loader
from dokimi.ingest.docling_loader import (
    convert_to_markdown,
    discover_documents,
    docling_available,
    is_supported,
    materialize_markdown,
)


def test_is_supported():
    assert is_supported("manual.pdf")
    assert is_supported("slides.PPTX")  # case-insensitive
    assert is_supported("scan.png")
    assert not is_supported("notes.md")
    assert not is_supported("data.csv")


def test_discover_documents_filters(tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "b.docx").write_text("x")
    (tmp_path / "c.md").write_text("x")
    (tmp_path / "d.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "e.pdf").write_text("x")

    found = {p.name for p in discover_documents(tmp_path)}
    assert found == {"a.pdf", "b.docx", "e.pdf"}

    non_recursive = {p.name for p in discover_documents(tmp_path, recursive=False)}
    assert "e.pdf" not in non_recursive


def test_discover_single_file(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_text("x")
    assert discover_documents(f) == [f]
    md = tmp_path / "y.md"
    md.write_text("x")
    assert discover_documents(md) == []


@pytest.mark.skipif(docling_available(), reason="docling installed; testing the missing path")
def test_convert_raises_clear_error_without_docling(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_text("x")
    with pytest.raises(ImportError, match="dokimi\\[ocr\\]"):
        convert_to_markdown(f)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_materialize_markdown_skips_a_dangling_link_at_the_first_name(tmp_path, monkeypatch):
    monkeypatch.setattr(docling_loader, "convert_to_markdown", lambda path: "# converted\n")
    out_dir = tmp_path / "md"
    out_dir.mkdir()
    link = out_dir / "a-pdf.md"
    try:
        link.symlink_to(Path("..") / "elsewhere.md")
    except OSError:
        pytest.skip("symlinks are not allowed here")
    written = materialize_markdown([tmp_path / "a.pdf"], out_dir)
    assert written == [out_dir / "a-pdf-2.md"]
    assert written[0].read_text(encoding="utf-8") == "# converted\n"
    assert not (tmp_path / "elsewhere.md").exists()
    assert link.is_symlink()
