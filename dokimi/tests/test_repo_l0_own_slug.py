# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""A code span naming the repository's own ``owner/name`` is ``ok`` (docs/repo-evaluation-contract.md, a path in a code span)."""

from __future__ import annotations

import pytest

from dokimi.repos.l0.core import Scan
from dokimi.repos.source import WorkTree

OWN = "this repository (source.url)"


def _resolve(tmp_path, span: str, url: str | None, files: tuple[str, ...] = ("README.md",)):
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    return Scan(WorkTree(tmp_path), frozenset(), repo_url=url).resolve_path("AGENTS.md", span)


@pytest.mark.parametrize("span", ["octo-org/demo", "Octo-Org/DEMO"])
def test_own_slug_is_ok_in_any_letter_case(tmp_path, span):
    assert _resolve(tmp_path, span, "https://github.com/octo-org/demo") == ("ok", OWN)


@pytest.mark.parametrize("url", ["https://github.com/octo-org/demo.git", "https://github.com/octo-org/demo/",
                                 "https://github.com/OCTO-org/Demo"])
def test_source_url_spellings_are_accepted(tmp_path, url):
    assert _resolve(tmp_path, "octo-org/demo", url) == ("ok", OWN)


def test_another_repository_stays_unresolved(tmp_path):
    assert _resolve(tmp_path, "octo-org/other", "https://github.com/octo-org/demo") == (
        "unresolved", "tried octo-org/other")


@pytest.mark.parametrize("url", [None, "https://gitlab.com/octo-org/demo"])
def test_no_github_source_url_keeps_the_verdict(tmp_path, url):
    assert _resolve(tmp_path, "octo-org/demo", url) == ("unresolved", "tried octo-org/demo")


@pytest.mark.parametrize("span", ["octo-org/demo/", "/octo-org/demo"])
def test_a_slash_around_the_slug_is_a_path(tmp_path, span):
    assert _resolve(tmp_path, span, "https://github.com/octo-org/demo") == ("unresolved", "tried octo-org/demo")


def test_a_tracked_directory_of_that_name_is_a_path(tmp_path):
    files = ("octo-org/demo/a.py",)
    url = "https://github.com/octo-org/demo"
    assert _resolve(tmp_path, "octo-org/demo", url, files) == ("ok", "octo-org/demo")
    assert _resolve(tmp_path, "octo-org/demo/a.py", url, files) == ("ok", "octo-org/demo/a.py")
