# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The prompt of an authoring task: the change's title and description without what would give it away.

Removed from the description: a fenced block that is a diff or quotes the change (half or more of its
lines of :data:`MIN_QUOTED`+ characters are lines the change adds or removes), a link to the change's commits or diff (a
``/commit/``, ``/commits/`` or ``/compare/`` URL, a ``.diff`` or ``.patch`` URL, or one to the pull
request itself), a word that is a prefix of one of its commits (7+ hex digits), and every line that names
a hidden test: its path, its file name without the extension, or a test function or class the change
defines in it. The lines dropped for the last reason are counted: more than :data:`MAX_LEAKED` and the
description is the tests, so the change gives no task.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

DEFAULT_INSTRUCTION = (
    "Implement the change described above in this repository, as its maintainers would merge it: code, "
    "documentation and anything else it needs. Tests for it exist but are hidden from you; they are added "
    "and run after you finish, so make the change complete and keep the existing tests passing."
)
MAX_LEAKED = 5
MIN_QUOTED = 4  # shorter lines (`}`, `else:`) prove no quote: only longer ones are compared with the change
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_URL = re.compile(r"https?://[^\s<>()\[\]]+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\((https?://[^)\s]+)\)")
_HEX = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
_DEFINED = re.compile(r"^\s*(?:async\s+def|def|class)\s+((?:test|Test)\w*)")
_DIFF_LINE = re.compile(r"^(?:diff --git |@@ -\d|\+\+\+ |--- a/)")


@dataclass(frozen=True)
class Prompt:
    text: str
    dropped: dict[str, int]  # test_lines, code_blocks, links


def test_names(hidden_tests: list[str], added: list[str]) -> list[str]:
    """What names a hidden test: each path, its file name without the extension, and each test function or class
    (``test*``, ``Test*``) defined on a line the change adds to a hidden test file."""
    names = set()
    for path in hidden_tests:
        names |= {path, posixpath.splitext(posixpath.basename(path))[0]}
    names |= {m.group(1) for line in added if (m := _DEFINED.match(line))}
    return sorted(n for n in names if n.lower() not in ("", "test", "tests"))  # a bare `test` names no one test


def build(title: str, body: str, *, names: list[str], changed_lines: set[str], number: int, commits: list[str],
          instruction: str = DEFAULT_INSTRUCTION) -> Prompt:
    """The prompt for a change titled ``title`` with description ``body``; ``changed_lines`` are the stripped
    lines its diff adds or removes, ``commits`` its SHAs."""
    dropped = {"test_lines": 0, "code_blocks": 0, "links": 0}
    lines = _without_quoted_blocks(body.replace("\r\n", "\n").split("\n"), changed_lines, dropped)
    named = [re.compile(r"(?<![\w.-])" + re.escape(n) + r"(?![\w-])") for n in names]
    kept = []
    for line in lines:
        line = _without_links(line, number, commits, dropped)
        if any(rx.search(line) for rx in named):
            dropped["test_lines"] += 1
            continue
        kept.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    head = " ".join(title.split()) or f"Pull request {number}"
    return Prompt(text="\n\n".join(p for p in (head, text, instruction.strip()) if p), dropped=dropped)


def _without_quoted_blocks(lines: list[str], changed: set[str], dropped: dict[str, int]) -> list[str]:
    """``lines`` without the fenced blocks that are a diff or quote the change; an unclosed fence runs to the end."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if m is None:
            out.append(lines[i])
            i += 1
            continue
        mark, info = m.group(1), m.group(2).strip().lower()
        end = next((j for j in range(i + 1, len(lines))
                    if lines[j].strip().startswith(mark[0] * len(mark)) and not lines[j].strip().strip(mark[0])),
                   len(lines))
        block = lines[i:end + 1]
        if _quotes(info, lines[i + 1:end], changed):
            dropped["code_blocks"] += 1
        else:
            out += block
        i = end + 1
    return out


def _quotes(info: str, inner: list[str], changed: set[str]) -> bool:
    words = [ln.strip() for ln in inner if len(ln.strip()) >= MIN_QUOTED]
    if info.split()[:1] in (["diff"], ["patch"]) or any(_DIFF_LINE.match(ln) for ln in inner):
        return True
    return bool(words) and 2 * sum(w in changed for w in words) >= len(words)


def _without_links(line: str, number: int, commits: list[str], dropped: dict[str, int]) -> str:
    def change_link(url: str) -> bool:
        return bool(re.search(rf"/commits?/|/compare/|\.(?:diff|patch)(?:[?#]|$)|/pulls?/{number}(?:\D|$)", url))

    def md(m: re.Match[str]) -> str:
        if not change_link(m.group(2)):
            return m.group(0)
        dropped["links"] += 1
        return ""

    def bare(m: re.Match[str]) -> str:
        if not change_link(m.group(0)):
            return m.group(0)
        dropped["links"] += 1
        return ""

    def sha(m: re.Match[str]) -> str:
        word = m.group(0).lower()
        if not any(c.startswith(word) for c in commits):
            return m.group(0)
        dropped["links"] += 1
        return ""

    return _HEX.sub(sha, _URL.sub(bare, _MD_LINK.sub(md, line)))
