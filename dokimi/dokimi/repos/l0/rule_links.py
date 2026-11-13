# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How the scan reads ``.claude/rules`` files and the files they link to."""

from __future__ import annotations

from ..source import RepoTree, SourceError


def loaded_text(path: str, text: str, rule_targets: frozenset[str] = frozenset()) -> str:
    """The text of ``path`` as the scan reads it: a ``.claude/rules`` file, or a file one links to, without its
    frontmatter, which Claude Code removes before it loads the rule."""
    from dokimi.repos.budget import RULE_GLOB
    from dokimi.repos.rule_frontmatter import blank_frontmatter

    return blank_frontmatter(text) if RULE_GLOB.match(path) or path in rule_targets else text


def rule_link_targets(tree: RepoTree, files: list[str] | set[str] | frozenset[str]) -> frozenset[str]:
    """The files a ``.claude/rules`` file links to. Claude Code loads each one as a rule, so its frontmatter goes
    whatever guidance name the file is also listed under (CLAUDE.local.md, or a link to one)."""
    from dokimi.repos.budget import RULE_GLOB

    out: set[str] = set()
    for f in files:
        if not RULE_GLOB.match(f):
            continue
        try:
            out.add(tree.resolve(f))
        except (SourceError, OSError, ValueError):
            continue
    return frozenset(out)
