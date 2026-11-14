# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown claim locations use source-file lines, not Unicode separators."""

import pytest

from dokimi.repos.l0.markdown import split


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\v", "\f", "\x1c", "\x1d", "\x1e"])
def test_unicode_separators_do_not_shift_markdown_claims(separator):
    text = f"Intro{separator}continued\n[link](missing.md) and `src/missing.py`\n```bash\nmake missing\n```\n"
    pieces = split(text)
    assert [(n, target) for n, target, _ in pieces.links] == [(2, "missing.md")]
    assert [(n, span) for n, span, _ in pieces.spans] == [(2, "src/missing.py")]
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(3, "bash", [(4, "make missing")])]


def test_unicode_separator_preserves_fence_matching_with_physical_locations():
    pieces = split("prose\u2028```bash\n[link](missing.md)\n")
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(1, "bash", [(2, "[link](missing.md)")])]
    assert pieces.links == []


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_scan_keeps_physical_line_numbers_after_unicode_separator(tmp_path, newline):
    from dokimi.repos import parse_manifest
    from dokimi.repos.l0 import scan_repo
    from dokimi.repos.source import WorkTree
    from tests.test_repo_l0 import MANIFEST, _repo

    guidance = newline.join(["Intro\u2028continued", "[link](missing.md)", "Paths: `src/missing.py`", ""])
    _repo(tmp_path, {"AGENTS.md": guidance, "src/keep.py": "", "CONTRIBUTING.md": ""})
    result = scan_repo(WorkTree(tmp_path), parse_manifest(MANIFEST))
    claims = {(c["kind"], c["claim"]): c["line"] for c in result["claims"]}
    assert claims[("link", "missing.md")] == 2
    assert claims[("path", "src/missing.py")] == 3


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_fences_keep_physical_newlines_and_trailing_blank_line(newline):
    pieces = split(newline.join(["intro", "```bash", "make all", "", ""]))
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(2, "bash", [(3, "make all"), (4, "")])]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\v", "\f", "\x1c", "\x1d", "\x1e"])
def test_unicode_separator_preserves_closing_fences_and_reference_definitions(separator):
    pieces = split(f"```bash\nmake all\n```{separator}Then run this\nSee [docs](missing.md).\n"
                   f"[x]: other.md{separator}more text\n")
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(1, "bash", [(2, "make all")])]
    assert [(n, target) for n, target, _ in pieces.links] == [(4, "missing.md"), (5, "other.md")]


def test_unicode_separator_preserves_span_and_info_string_matching():
    pieces = split("`src/keep.py\u2028and src/missing.py`\n```\u2028bash\nmake all\n```\n")
    assert pieces.spans == []
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(2, "", [(2, "bash"), (3, "make all")])]


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\v", "\f", "\x1c", "\x1d", "\x1e"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_unicode_separator_preserves_blank_fenced_segments(separator, newline):
    pieces = split(newline.join(["```bash", f"make all{separator}", "```", ""]))
    assert [(f.line, f.lang, f.lines) for f in pieces.fences] == [(1, "bash", [(2, "make all"), (2, "")])]
