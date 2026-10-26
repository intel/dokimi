# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Coverage scoring heuristic for markdown docs."""

from __future__ import annotations

import re


HEADING_RE = re.compile(r"#{1,6}(?:\s|$)")
FENCE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)$")


def fenced_lines(lines: list[str]) -> tuple[list[bool], int]:
    """Mark each line that belongs to a fenced block, fences included.

    Returns the marks and the number of closed blocks. A fence opens with 3+
    backticks or tildes and closes on the same character, at least as long, with
    nothing after it. A block left open at the end is marked but not counted.
    """
    inside = [False] * len(lines)
    blocks = 0
    fence = ""
    for i, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if not fence:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = m.group(1)
                inside[i] = True
        else:
            inside[i] = True
            if (
                m
                and m.group(1)[0] == fence[0]
                and len(m.group(1)) >= len(fence)
                and not m.group(2).strip()
            ):
                fence = ""
                blocks += 1
    return inside, blocks


def _count_headings_and_blocks(text: str) -> tuple[int, int]:
    """Count headings outside code and closed fenced blocks.

    A ``#`` line inside a fence is a code comment, not a heading.
    """
    lines = re.split(r"\r\n|\r|\n", text)
    inside, blocks = fenced_lines(lines)
    headings = sum(1 for line, code in zip(lines, inside, strict=True) if not code and HEADING_RE.match(line))
    return headings, blocks


def score(text: str) -> float:
    """Return simple structure/body coverage score in [0, 1]."""
    if not text.strip():
        return 0.0
    headings, code_blocks = _count_headings_and_blocks(text)
    words = len(text.split())

    heading_signal = min(headings / 6.0, 1.0)
    code_signal = min(code_blocks / 3.0, 1.0)
    body_signal = min(words / 500.0, 1.0)

    return round(0.4 * heading_signal + 0.3 * code_signal + 0.3 * body_signal, 4)
