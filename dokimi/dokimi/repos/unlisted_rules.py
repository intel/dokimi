# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``.claude/rules`` files ``repo budget`` cannot count, because the tree does not list them: one warning each."""

from __future__ import annotations

import re

from dokimi.repos.source import RepoTree

RULES_ENTRY = re.compile(r"^(?:.+/)?\.claude/rules(?:/.+)?$")
_UNRESOLVED = (OSError, ValueError, RuntimeError)  # out of the tree, dangling, or a loop (RuntimeError before 3.13)


def unlisted_rules(tree: RepoTree, files: list[str]) -> list[str]:
    """Warnings for the rules behind a root ``.claude`` link, which Claude Code reads at start through it."""
    if ".claude" not in files:  # a real .claude directory is not an entry; a link to one is
        return []
    try:
        target = tree.resolve(".claude", dirs=True)
    except _UNRESOLVED:
        return [".claude: a link that does not resolve in the tree; any rules behind it are not counted"]
    if tree.is_dir(f"{target}/rules"):
        return [".claude: a link to a directory; the rules behind it are not counted at start"]
    return []
