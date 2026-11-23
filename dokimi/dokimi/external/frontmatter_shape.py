# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What shape an unloaded ``SKILL.md`` has: a broken skill (its load error stands) or prose.

Kept out of :mod:`dokimi.external.validate`, which reads the result in ``_unloaded_review``.
"""

from __future__ import annotations

import re

from .skill_format import FORMAT_AGENT_SKILL
from .skill_markdown import outside_fences

def has_late_frontmatter(lines: list[str]) -> bool:
    """Whether a ``---`` line outside a code fence, in the first 20 physical lines, has a ``name:`` or ``description:`` line in its block (up to the next ``---``, outside a fence)."""
    for i in range(min(len(lines) - 1, 20)):
        # A fence's state depends only on the lines above it, so the prefix tells whether line i is outside one.
        above = outside_fences("\n".join(lines[: i + 1]))
        if lines[i].rstrip() != "---" or not above or above[-1].rstrip() != "---":
            continue
        for line in outside_fences("\n".join(lines[i + 1 :])):
            if line.rstrip() == "---":
                break
            if line.startswith(("name:", "description:")):
                return True
    return False


# A YAML key at the start of a line: ``title: x``, ``  name: x``, ``"name": x``, ``{name: x``, ``- name: x``.
YAML_KEY_RE = re.compile(r"""\s*(?:-\s+)?(?:[{\[]\s*)?["']?[\w][\w.-]*["']?\s*:""")


FLOW_OPENER_RE = re.compile(r"[\[{](?![^\]\n]*\](?:\(|\[|:))")  # a flow collection, not a link ``[text](url)``, ``[text][ref]`` or ``[ref]: url``


def _first_line(block: list[str]) -> str:
    """The first line of ``block`` that is not blank, stripped."""
    return next((line.strip() for line in block if line.strip()), "")


def opens_frontmatter(lines: list[str]) -> bool:
    """Whether the lines open with a ``---`` block that is empty or holds a YAML key (``title:``, ``name:``) before its closing ``---``.

    A code fence in the block is prose, not YAML: its lines are left out, so a ``---`` rule followed by an example fence stays a rule.
    """
    block: list[str] = []
    for line in outside_fences("\n".join(lines))[1:]:
        if line.rstrip() == "---":
            return not any(line.strip() for line in block) or any(YAML_KEY_RE.match(line) for line in block) or bool(FLOW_OPENER_RE.match(_first_line(block)))
        block.append(line)
    first = _first_line(block)
    return bool(YAML_KEY_RE.match(first) or FLOW_OPENER_RE.match(first))  # unclosed: a later ``Usage:`` line is prose, not a key


def keeps_load_error(fmt: str, lines: list[str], first: str, unquoted: list[str]) -> bool:
    """Whether an unloaded SKILL.md is a broken skill, whose load error stands, rather than prose.

    Frontmatter after blank lines, an indent or another line is broken. So is, under the spec only, a ``name:`` block
    after prose; OpenCode and a host with no spec read a file that does not start with ``---`` as Markdown. So is an
    unclosed block there, which is no missing description.
    """
    if first.strip() == "---":
        if lines[0].rstrip() != "---":
            return True
        return fmt != FORMAT_AGENT_SKILL and opens_frontmatter(unquoted) and not any(l.rstrip() == "---" for l in lines[1:])
    return fmt == FORMAT_AGENT_SKILL and has_late_frontmatter(unquoted)
