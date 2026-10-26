# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`ingest docling`: an unusable --out-dir exits 2 with a message, not a traceback."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from dokimi.ingest import docling_loader


@pytest.fixture
def fake_docling(monkeypatch):
    monkeypatch.setattr(docling_loader, "docling_available", lambda: True)
    monkeypatch.setattr(docling_loader, "convert_to_markdown", lambda path: f"# {path.stem}\n")


def _ingest(src, out_dir):
    args = build_parser().parse_args(
        ["ingest", "docling", "--input", str(src), "--out-dir", str(out_dir)])
    args.func(args)


def test_an_out_dir_that_is_a_file_exits_2_with_its_path(tmp_path, capsys, fake_docling):
    src = tmp_path / "a.pdf"
    src.write_text("x")
    out = tmp_path / "out"
    out.write_text("not a directory")
    with pytest.raises(SystemExit) as exc:
        _ingest(src, out)
    assert exc.value.code == 2
    assert f"Error: ingest into {out} failed" in capsys.readouterr().err


def test_an_os_error_while_converting_exits_2_with_the_reason(tmp_path, capsys, monkeypatch):
    src = tmp_path / "a.pdf"
    src.write_text("x")
    monkeypatch.setattr(docling_loader, "docling_available", lambda: True)

    def unreadable(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(docling_loader, "convert_to_markdown", unreadable)
    with pytest.raises(SystemExit) as exc:
        _ingest(src, tmp_path / "out")
    assert exc.value.code == 2
    assert "Permission denied" in capsys.readouterr().err


def test_a_usable_out_dir_still_gets_the_markdown(tmp_path, capsys, fake_docling):
    src = tmp_path / "a.pdf"
    src.write_text("x")
    out = tmp_path / "out"
    _ingest(src, out)
    assert (out / "a-pdf.md").read_text(encoding="utf-8") == "# a\n"
    assert "Materialized 1 Markdown file(s)" in capsys.readouterr().out
