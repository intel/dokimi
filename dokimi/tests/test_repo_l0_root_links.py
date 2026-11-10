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
