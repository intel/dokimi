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
    scan = Scan(WorkTree(tmp_path), frozenset(), repo_url=url)
    scan.check_span("AGENTS.md", 1, span, span)
    [claim] = [c for c in scan.claims if c.kind == "path"]
    return claim.status, claim.why


@pytest.mark.parametrize(("command", "kind", "operand"), [
    ("pytest octo-org/demo", "pytest_path", "octo-org/demo"),
    ("python octo-org/demo.py", "script_path", "octo-org/demo.py"),
])
def test_a_command_operand_is_a_path_not_the_slug(tmp_path, command, kind, operand):
    (tmp_path / "octo-org").mkdir()
    (tmp_path / "octo-org" / "keep.py").write_text("x\n", encoding="utf-8")
    scan = Scan(WorkTree(tmp_path), frozenset({"pytest"}), repo_url=f"https://github.com/{operand}")
    scan.check_span("AGENTS.md", 1, command, command)
    [claim] = [c for c in scan.claims if c.kind == kind]
    assert (claim.claim, claim.status) == (operand, "fail")


@pytest.mark.parametrize("span", ["octo-org/demo", "Octo-Org/DEMO"])
def test_own_slug_is_ok_in_any_letter_case(tmp_path, span):
    assert _resolve(tmp_path, span, "https://github.com/octo-org/demo") == ("ok", OWN)


@pytest.mark.parametrize("url", ["https://github.com/octo-org/demo.git", "https://github.com/octo-org/demo/",
                                 "https://github.com/octo-org/demo.git/", "https://github.com/OCTO-org/Demo",
                                 "https://GitHub.com/octo-org/demo"])
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
