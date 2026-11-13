# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``SKILL.md`` with no frontmatter: plain Markdown, not a broken skill.

Such a file does not load (the loader's error says "no frontmatter"). :mod:`dokimi.external.validate`
gives it one ``error`` finding, ``not-a-skill-no-frontmatter``, whose message holds a frontmatter block
made from the file's H1 and first paragraph, and ``catalog check`` counts these apart from the other
load errors in its summary. In the GitSkills corpus they are 165K of 1.88M distinct ``SKILL.md`` files,
141K of them starting with a ``#`` heading.
"""

from __future__ import annotations

import json
import re

from dokimi.external.skill_markdown import outside_fences

NO_FRONTMATTER = "not-a-skill-no-frontmatter"
NAME_MAX = 64
SPEC_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # the spec's name pattern (validate.NAME_PATTERN)
_NON_PRINTABLE = re.compile(r"[^\t\n\r\x20-\x7e\x85\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")  # PyYAML's reader
DESCRIPTION_HINT_MAX = 300
SETEXT_UNDERLINE_RE = re.compile(r"=+")  # the underline of a setext H1 (a --- one is a rule or an H2)


def no_frontmatter_message(lines: list[str], dir_name: str, at_root: bool = False) -> str:
    """The ``not-a-skill-no-frontmatter`` message, with a frontmatter block made from the H1 and the first paragraph.

    Below the scanned root the name is the directory's when that is a valid spec name, since the
    loader's ``name-mismatch`` check compares the name with it; otherwise (and at the root) it is the
    H1 in kebab-case (ASCII letters and digits; the directory name when the H1 has none, or there is
    no H1). The description is the first paragraph after the H1, cut at a word near 300 characters.
    The block is a start: the description still needs a "when" clause.
    """
    # CommonMark fences: a shorter run or another marker does not close one
    body = _without_comments([line.strip().lstrip("\ufeff") for line in outside_fences("\n".join(lines))])
    title = ""
    start = 0  # the paragraph is the one after the first H1: an intro or a badge above it is not taken
    for i, stripped in enumerate(body):
        if stripped[:2] in ("# ", "#\t"):  # CommonMark: a space or a tab after the #
            title = stripped[2:].strip()
            start = i + 1
            break
        if stripped and i + 1 < len(body) and SETEXT_UNDERLINE_RE.fullmatch(body[i + 1]):  # ``Title`` over ``===``
            title = stripped
            start = i + 2
            break
    paragraph = _first_paragraph(body[start:])
    if not at_root and SPEC_NAME.fullmatch(dir_name) and len(dir_name) <= NAME_MAX:
        name = dir_name  # validate.NAME_PATTERN, as the loader's name-mismatch check reads it
    else:
        name = _kebab(title) or _kebab(dir_name) or "my-skill"
    description = _NON_PRINTABLE.sub("", " ".join(paragraph))  # what PyYAML refuses in a plain or quoted scalar
    if len(description) > DESCRIPTION_HINT_MAX:
        description = description[:DESCRIPTION_HINT_MAX].rsplit(" ", 1)[0] + " ..."
    description = description or f"What {name} does. Use when ..."
    # Always quoted: YAML reads a name such as "2048", "yes" or "2026-02-30" as a number, a bool or a date.
    name_text = json.dumps(name)
    block = f"---\nname: {name_text}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---"
    return (
        "plain Markdown with no frontmatter: no harness offers it as a skill. To make it one, start "
        f"the file with (then add when to use it to the description):\n{block}"
    )


def _without_comments(body: list[str]) -> list[str]:
    """``body`` with its HTML comment lines taken out, a multi-line comment up to the line holding ``-->``."""
    kept: list[str] = []
    in_comment = False
    for stripped in body:
        if in_comment:
            in_comment = "-->" not in stripped
        elif stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
        else:
            kept.append(stripped)
    return kept


def _first_paragraph(body: list[str]) -> list[str]:
    """The lines of the first paragraph of ``body``, skipping blank, heading, table and HTML lines."""
    paragraph: list[str] = []
    for stripped in body:
        if not stripped or stripped.startswith(("#", "|", "<", "!", "---")):
            if paragraph:
                break
            continue
        paragraph.append(stripped)  # outside_fences has taken the quote markers off already
    return paragraph


def _kebab(text: str) -> str:
    """``text`` as a spec name: ASCII lowercase letters and digits joined by single hyphens, at most 64 characters."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:NAME_MAX].strip("-")
