# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Static checks of an ``llms.txt`` file: the agent-facing index of a docs site.

A prototype (docs/proposals/lightsage-inspired-features.md at the repository root):
no network and no LLM. It checks the structure the llms.txt convention asks for and the
positioning sections agents use to decide when a tool applies and when it does not.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass

#: Above this an index stops being a cheap first read; the llms.txt convention says compact.
TOKEN_BUDGET = 2000

_H1 = re.compile(r"^ {0,3}#\s+\S")
_H2 = re.compile(r"^ {0,3}##\s+(.+?)\s*$")
_LINK_START = re.compile(r"(?<![!\\])\[([^\]]+)\]\(")
_LINK_END = re.compile(r"""(?:[ \t]+(?:"[^"]*"|'[^']*'))?\)""")
_DESTINATION_MARK = re.compile(r"\\.|[()\s]")
_WHEN_NOT = re.compile(r"\bwhen\s+not\b|\bnot\s+for\b|\bavoid\b|\balternatives?\b", re.I)
_WHEN = re.compile(r"\bwhen\s+to\s+use\b|\buse\s+(it\s+)?(for|when)\b|\buse\s+cases?\b", re.I)


@dataclass(frozen=True)
class Finding:
    """One problem: a stable check id, its severity, where, what is wrong and the fix."""

    check: str
    severity: str  # "fail" | "warn"
    line: int
    message: str
    fix: str
    identity: str = ""

    def as_dict(self) -> dict:
        fields = asdict(self)
        del fields["identity"]
        return fields

def estimate_tokens(text: str) -> int:
    """Rough token count, four characters a token, enough for a budget check."""
    return (len(text) + 3) // 4


def _title_findings(lines: list[str]) -> list[Finding]:
    h1 = [i for i, line in enumerate(lines, 1) if _H1.match(line)]
    if not h1:
        return [Finding("title", "fail", 1, "no H1 title",
                        "Open the file with `# <Product name>`.")]
    found = []
    if len(h1) > 1:
        found.append(Finding("title", "warn", h1[1], f"{len(h1)} H1 titles",
                             "Keep one H1; make the others `##` sections."))
    after = [line for line in lines[h1[0]:] if line.strip()]
    if not after or not re.match(r"^ {0,3}>\s*\S", after[0]):
        found.append(Finding("summary", "fail", h1[0] + 1, "no blockquote summary under the title",
                             "Add `> <one line: what it does, for whom>` after the H1."))
    return found


def _section_findings(lines: list[str]) -> list[Finding]:
    heads = [m.group(1) for line in lines if (m := _H2.match(line))]
    found = []
    if not any(_WHEN.search(h) and not _WHEN_NOT.search(h) for h in heads):
        found.append(Finding("when_to_use", "warn", 0, "no 'when to use' section",
                             "Add `## When to use` listing the concrete tasks it fits."))
    if not any(_WHEN_NOT.search(h) for h in heads):
        found.append(Finding("when_not_to_use", "warn", 0, "no 'when not to use' section",
                             "Add `## When not to use` naming the cases and the alternative."))
    return found


def _links(line: str) -> Iterator[tuple[str, str, int]]:
    for label in _LINK_START.finditer(line):
        depth = 0
        for mark in _DESTINATION_MARK.finditer(line, label.end()):
            char = mark[0]
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == ")" or char.isspace():
                end = _LINK_END.match(line, mark.start()) if depth == 0 else None
                if end is not None and mark.start() > label.end():
                    yield label[1], line[label.end():mark.start()], end.end()
                break


def _link_findings(lines: list[str]) -> list[Finding]:
    links = [(i, title, target, end) for i, line in enumerate(lines, 1) for title, target, end in _links(line)]
    if not links:
        return [Finding("links", "fail", 0, "no Markdown links to the documentation",
                        "List key pages as `- [Title](url): one-line description`.")]
    return [Finding("links", "warn", i, f"link to {target} has no description",
                    "Add `: <what the page answers>` after the link.", f"{title}|{target}")
            for i, title, target, end in links
            if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", lines[i - 1])
            and not re.match(r"\s*:\s*\S", lines[i - 1][end:])]


def _without_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    parts: list[str] = []
    while line:
        if in_comment:
            _, closed, line = line.partition("-->")
            if not closed:
                return "".join(parts), True
            in_comment = False
        else:
            visible, opened, line = line.partition("<!--")
            parts.append(visible)
            in_comment = bool(opened)
    return "".join(parts), in_comment


def _structure_lines(text: str) -> list[str]:
    """Skip fenced examples while preserving physical line numbers."""
    lines = []
    fence = ""
    in_comment = False
    for line in text.removeprefix("\ufeff").splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            lines.append("")
        elif marker and not in_comment and (marker[1][0] == "~" or "`" not in marker[2]):
            fence = marker[1]
            lines.append("")
        else:
            line, in_comment = _without_comments(line, in_comment)
            lines.append(line)
    return lines


def check(text: str, *, token_budget: int = TOKEN_BUDGET) -> list[Finding]:
    """Every finding for one llms.txt text, in check order; empty when it passes."""
    lines = _structure_lines(text)
    found = _title_findings(lines) + _section_findings(lines) + _link_findings(lines)
    tokens = estimate_tokens(text)
    if tokens > token_budget:
        found.append(Finding("size", "warn", 0, f"~{tokens} tokens, over {token_budget}",
                             "Move detail into linked pages or an llms-full.txt."))
    return found
