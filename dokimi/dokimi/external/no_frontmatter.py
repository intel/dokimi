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

import yaml

NO_FRONTMATTER = "not-a-skill-no-frontmatter"
NAME_MAX = 64
DESCRIPTION_HINT_MAX = 300


def no_frontmatter_message(lines: list[str], dir_name: str) -> str:
    """The ``not-a-skill-no-frontmatter`` message, with a frontmatter block made from the H1 and the first paragraph.

    The name is the H1 in kebab-case (ASCII letters and digits; the directory name when the H1 has
    none, or there is no H1), and the description is the first paragraph after it, cut at a word
    near 300 characters. The block is a start: the description still needs a "when" clause.
    """
    title, in_fence = "", False
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith(("#", "|", "<", "!", "---")):
            if paragraph:
                break
            continue
        paragraph.append(stripped.lstrip("> ") if stripped.startswith(">") else stripped)  # a quote's text
    name = _kebab(title) or _kebab(dir_name) or "my-skill"
    description = " ".join(paragraph)
    if len(description) > DESCRIPTION_HINT_MAX:
        description = description[:DESCRIPTION_HINT_MAX].rsplit(" ", 1)[0] + " ..."
    description = description or f"What {name} does. Use when ..."
    # A name such as "2048" or "yes" reads back as a number or a bool: quote it then.
    name_text = name if isinstance(yaml.safe_load(name), str) else json.dumps(name)
    block = f"---\nname: {name_text}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---"
    return (
        "plain Markdown with no frontmatter: no harness offers it as a skill. To make it one, start "
        f"the file with (then add when to use it to the description):\n{block}"
    )


def _kebab(text: str) -> str:
    """``text`` as a spec name: ASCII lowercase letters and digits joined by single hyphens, at most 64 characters."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:NAME_MAX].strip("-")
