# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1207: ``local:`` and ``okf:`` refuse a doc tree with a directory they cannot list, not a smaller doc set."""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dokimi.mcp import MCPConnectionError
from dokimi.mcp.factory import require_local_doc_source
from dokimi.mcp.local_markdown import LocalMarkdownClient
from dokimi.mcp.okf_client import OKFClient
from dokimi.utils import InputError

CLIENTS = [(LocalMarkdownClient, "local"), (OKFClient, "okf")]

needs_modes = pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("alpha parallel_for\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("beta parallel_for\n", encoding="utf-8")
    return root


def _files(client) -> list[str]:
    return [d["file"] for d in client.get_library_docs("x", "parallel_for")]


def _lock(directory: Path) -> None:
    directory.chmod(0o111)
    if os.access(directory, os.R_OK):
        directory.chmod(0o700)
        pytest.skip("this file system or user lists a mode 111 directory")


@pytest.mark.parametrize("cls,_prefix", CLIENTS)
def test_a_listable_nested_tree_gives_the_same_files(tmp_path: Path, cls, _prefix):
    root = _tree(tmp_path)
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "c.md").write_text("gamma\n", encoding="utf-8")
    (root / "sub" / "skip.bin").write_text("x\n", encoding="utf-8")
    assert sorted(_files(cls(root))) == ["a.md", "sub/b.md", "sub/deep/c.md"]


@needs_modes
@pytest.mark.parametrize("cls,_prefix", CLIENTS)
def test_a_subdirectory_that_cannot_be_listed_is_refused(tmp_path: Path, caplog, cls, _prefix):
    root = _tree(tmp_path)
    client = cls(root)
    _lock(root / "sub")
    try:
        with pytest.raises(MCPConnectionError, match=rf"cannot list {re.escape(str(root / 'sub'))}: "):
            client.get_library_docs("x", "parallel_for")
        with caplog.at_level(logging.WARNING):
            assert client.check_connection() is False
        assert any("sub" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    finally:
        (root / "sub").chmod(0o700)
    assert sorted(_files(client)) == ["a.md", "sub/b.md"]
    assert client.check_connection() is True


@needs_modes
@pytest.mark.parametrize("cls,_prefix", CLIENTS)
def test_a_root_that_cannot_be_listed_is_refused(tmp_path: Path, cls, _prefix):
    root = _tree(tmp_path)
    _lock(root)
    try:
        with pytest.raises(MCPConnectionError, match="cannot list"):
            cls(root).get_library_docs("x", "parallel_for")
    finally:
        root.chmod(0o700)


@needs_modes
@pytest.mark.parametrize("cls,_prefix", CLIENTS)
def test_a_file_that_cannot_be_read_is_still_skipped(tmp_path: Path, cls, _prefix):
    root = _tree(tmp_path)
    (root / "sub" / "b.md").chmod(0)
    if os.access(root / "sub" / "b.md", os.R_OK):
        pytest.skip("this user reads a mode 000 file")
    try:
        assert _files(cls(root)) == ["a.md"]
    finally:
        (root / "sub" / "b.md").chmod(0o600)


@needs_modes
@pytest.mark.parametrize("cls,prefix", CLIENTS)
def test_the_preflight_refuses_a_subdirectory_that_cannot_be_listed(tmp_path: Path, cls, prefix):
    root = _tree(tmp_path)
    require_local_doc_source(f"{prefix}:{root}")
    _lock(root / "sub")
    try:
        with pytest.raises(InputError, match=rf"cannot list {re.escape(str(root / 'sub'))}: Permission denied$"):
            require_local_doc_source(f"{prefix}:{root}")
    finally:
        (root / "sub").chmod(0o700)


@needs_modes
@pytest.mark.parametrize("cls,prefix", CLIENTS)
def test_the_preflight_keeps_permission_denied_for_a_root_that_cannot_be_listed(tmp_path: Path, cls, prefix):
    """#1207 and docs/adding-doc-source.md: an unreadable root keeps the #1086 message, not ``cannot list``."""
    root = _tree(tmp_path)
    _lock(root)
    try:
        with pytest.raises(InputError, match=rf"cannot read {re.escape(str(root))}: permission denied") as caught:
            require_local_doc_source(f"{prefix}:{root}")
    finally:
        root.chmod(0o700)
    assert "cannot list" not in str(caught.value)


LISTING_ERRORS = [
    (OSError(errno.EIO, "Input/output error"), "Input/output error"),
    (OSError("boom"), "boom"),
]


def _failing(err: OSError):
    def listing(_root):
        raise err

    return listing


@pytest.mark.parametrize("err,_strerror", LISTING_ERRORS)
@pytest.mark.parametrize("cls,_prefix", CLIENTS)
def test_a_listing_error_without_a_filename_names_the_root(tmp_path: Path, monkeypatch, cls, _prefix, err, _strerror):
    """#1207: an ``OSError`` that carries no filename is reported against the source directory."""
    root = _tree(tmp_path)
    monkeypatch.setattr("dokimi.mcp.utils.listed_entries", _failing(err))
    with pytest.raises(MCPConnectionError) as caught:
        cls(root).get_library_docs("x", "parallel_for")
    assert str(caught.value) == f"{cls.__name__}: cannot list {root}: {err}"


@pytest.mark.parametrize("err,strerror", LISTING_ERRORS)
@pytest.mark.parametrize("cls,prefix", CLIENTS)
def test_the_preflight_names_the_root_for_a_listing_error_without_a_filename(tmp_path: Path, monkeypatch, cls, prefix, err, strerror):
    """#1207 and docs/adding-doc-source.md: the preflight message ends with the strerror, or the error when it has none."""
    root = _tree(tmp_path)
    monkeypatch.setattr("dokimi.digest_entries.listed_entries", _failing(err))
    with pytest.raises(InputError) as caught:
        require_local_doc_source(f"{prefix}:{root}")
    assert str(caught.value) == f"doc source {prefix}:{root}: cannot list {root}: {strerror}"


@needs_modes
def test_questions_generate_stops_before_any_seed_extraction(tmp_path: Path, monkeypatch):
    from dokimi.commands import questions as cmd
    from dokimi import questions as questions_pkg

    root = _tree(tmp_path)
    reached = MagicMock(side_effect=AssertionError("reached the extractor or generator"))
    monkeypatch.setattr(questions_pkg, "QuestionGenerator", reached)
    monkeypatch.setattr(questions_pkg, "RagasSeedExtractor", reached)
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps({"personas": [{"id": "dev", "name": "dev", "skill_level": "expert"}]}), encoding="utf-8")
    args = argparse.Namespace(
        product="oneTBB", personas=str(personas), topics=None, doc_source=f"local:{root}", context7_id=None,
        top_k=3, provider="openai", model="m", count=1, validate=False, output=None)
    _lock(root / "sub")
    try:
        with pytest.raises(InputError, match="cannot list .*sub"):
            cmd.cmd_questions_generate(args)
    finally:
        (root / "sub").chmod(0o700)
    reached.assert_not_called()
