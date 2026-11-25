# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reach probe workspace seeds a guidance file whose name has a byte that is not UTF-8 (#1156)."""

from __future__ import annotations

import os
import sys

import pytest

from dokimi import process
from dokimi.repos import WorkTree
from dokimi.repos import reach

NON_UTF8 = os.fsdecode(b"n\xff.md")
needs_posix_names = pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="the file system takes only UTF-8 names")


def _build(tmp_path, name, text):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / name).write_bytes(text)
    ws = tmp_path / "ws"
    ws.mkdir()
    seeded = reach.build_workspace(WorkTree(repo), [name], ws, "s", runner=process.FakeRunner())
    return ws, seeded


@needs_posix_names
def test_build_workspace_seeds_a_file_named_with_a_non_utf8_byte(tmp_path):
    ws, seeded = _build(tmp_path, NON_UTF8, b"hello\n")
    assert [s.path for s in seeded] == [NON_UTF8]
    written = (ws / NON_UTF8).read_bytes()
    assert written == (f"Verification token (head) for n?.md: {seeded[0].head}\n\nhello\n"
                       f"\n\nVerification token (tail) for n?.md: {seeded[0].tail}\n").encode()


@needs_posix_names
def test_write_shadow_seeds_a_file_named_with_a_non_utf8_byte(tmp_path):
    item = reach.SeededFile(NON_UTF8, "RC000001", "RC000002")
    dirs, written = [], []
    reach._write_shadow(tmp_path, item, dirs, written)
    assert written == [tmp_path / NON_UTF8]
    assert (tmp_path / NON_UTF8).read_bytes() == (
        "Verification token (head) for n?.md: RC000001\n\n# Local instructions\n"
        "\n\nVerification token (tail) for n?.md: RC000002\n").encode()


@pytest.mark.skipif(os.linesep != "\n", reason="write_text wrote os.linesep")
def test_build_workspace_writes_the_bytes_write_text_wrote_for_a_utf8_name(tmp_path):
    ws, seeded = _build(tmp_path, "é.md", b"\xef\xbb\xbf---\na: 1\n---\nhi\xc3\xa9")
    head, tail = seeded[0].head, seeded[0].tail
    assert (ws / "é.md").read_bytes() == (
        f"﻿---\na: 1\n---\nVerification token (head) for é.md: {head}\n\nhié\n"
        f"\n\nVerification token (tail) for é.md: {tail}\n").encode()
