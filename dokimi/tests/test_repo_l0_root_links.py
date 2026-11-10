# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""``repo scan`` below a root link to a directory reads the tree as a checkout lays it out."""

from __future__ import annotations

import pytest

from dokimi.repos.l0 import scan_repo
from tests.test_repo_l0_cases import _by_file, _linked_repo, _manifest


@pytest.mark.parametrize("ignore, rule, link", [
    ("src/pkg/.gitignore", "*.cfg", True),  # the rule sits in the directory the link names
    (".gitignore", "src/pkg/*.cfg", True),  # a root rule names the real path
    ("owner/.gitignore", "*.cfg", False),  # a real `owner/`
], ids=["target-rule", "root-rule", "real-dir"])
def test_a_path_below_a_root_link_its_target_gitignore_ignores_stays_unresolved(tmp_path, ignore, rule, link):
    """A build output named through the link is ``unresolved``, as it is below a real ``owner/``."""
    files = {"AGENTS.md": "Run `owner/gen.cfg` and `owner/*.cfg`.\n", "ev/tasks/t/task.json": "{}",
             "src/pkg/x": "", ignore: rule + "\n", **({} if link else {"owner/x": ""})}
    got = _by_file(scan_repo(_linked_repo(tmp_path / "r", files, {"owner": "src/pkg"} if link else {}), _manifest()))
    cited = f"; {ignore}:1 (`{rule}`) ignores it, so it is likely generated rather than tracked"
    assert got[("AGENTS.md", "owner/gen.cfg")] == ("unresolved", "owner/ exists but owner/gen.cfg does not" + cited)
    assert got[("AGENTS.md", "owner/*.cfg")] == ("unresolved", "owner/ exists but nothing in it matches *.cfg" + cited)


@pytest.mark.parametrize("target", ["src/a*", "src/a?"])
def test_a_wildcard_in_a_link_target_name_does_not_match_a_sibling_directory(tmp_path, target):
    """``owner -> src/a*`` holds only ``x``: ``src/ab/x.cfg`` beside it is no match of ``owner/*.cfg``."""
    files = {"AGENTS.md": "Run `owner/*.cfg`.\n", "ev/tasks/t/task.json": "{}", f"{target}/x": "", "src/ab/x.cfg": ""}
    got = _by_file(scan_repo(_linked_repo(tmp_path / "r", files, {"owner": target}), _manifest()))
    assert got[("AGENTS.md", "owner/*.cfg")] == ("fail", "owner/ exists but nothing in it matches *.cfg")


@pytest.mark.parametrize("ignore", ["src/a*/.gitignore", "src/ax/.gitignore"], ids=["target-rule", "sibling-rule"])
def test_a_glob_below_a_link_to_a_wildcard_name_asks_only_that_directory_s_gitignore(tmp_path, ignore):
    """``owner -> src/a*``: a rule in ``src/a*/`` ignores ``owner/*.cfg``, one in the sibling ``src/ax/`` does not."""
    files = {"AGENTS.md": "Run `owner/*.cfg`.\n", "ev/tasks/t/task.json": "{}", "src/a*/x": "", ignore: "*.cfg\n"}
    got = _by_file(scan_repo(_linked_repo(tmp_path / "r", files, {"owner": "src/a*"}), _manifest()))
    why = "owner/ exists but nothing in it matches *.cfg"
    cited = f"; {ignore}:1 (`*.cfg`) ignores it, so it is likely generated rather than tracked"
    want = ("unresolved", why + cited) if ignore.startswith("src/a*") else ("fail", why)
    assert got[("AGENTS.md", "owner/*.cfg")] == want


@pytest.mark.parametrize("target", [".", "src/.."])
def test_a_glob_below_a_root_link_to_the_root_matches_at_the_root(tmp_path, target):
    """``owner -> .`` lists the root under ``owner/``, so ``x.cfg`` there matches ``owner/*.cfg``."""
    files = {"AGENTS.md": "Run `owner/*.cfg`.\n", "ev/tasks/t/task.json": "{}", "src/keep": "", "x.cfg": ""}
    got = _by_file(scan_repo(_linked_repo(tmp_path / "r", files, {"owner": target}), _manifest()))
    assert got[("AGENTS.md", "owner/*.cfg")] == ("ok", "glob owner/*.cfg -> e.g. owner/x.cfg")
