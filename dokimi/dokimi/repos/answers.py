# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Final-answer conversions a harness profile names in ``answer_format`` (contract §6).

A native review mode answers in its own format, not the ```` ```json ```` block
a review task asks for. The conversion keeps the harness's text and appends
that block, so a grader reading the last ```` ```json ```` block of the answer
reads the native findings; nothing is dropped from what the agent said.

Findings are ``{file, line, end_line, severity, priority, rule, message}``:
``file`` relative to the workspace, ``line`` the first line of the range,
``severity`` ``blocker`` (P0), ``major`` (P1) or ``minor`` (P2, P3) and ``None`` for a
priority this module does not know, ``rule`` the finding's title and ``message`` its body.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from .transcript import relative_path

log = logging.getLogger(__name__)

ANSWER_FORMATS = ("codex-review",)
_SEVERITY = {"P0": "blocker", "P1": "major", "P2": "minor", "P3": "minor"}
# a header may be indented (a finding in a list); the title may hold " — ": the path part is what follows
# the last one, holds none, and stays on the header's line
_CODEX_FINDING = re.compile(r"^[ \t]*- \[(P\d)\] (.+) — ([^\s—][^—\r\n]*?):(\d+)(?:-(\d+))?[ \t\r]*$", re.M)
# a marker is a [Pn] header in any shape outside a parsed finding: a list item (``-``, ``*``, ``1.``, ``2)``) at
# any indent (bold or not, with a path or not) or a line starting with [Pn]; every marker is parsed or no block
_BULLET_MARK = re.compile(r"^[ \t]*(?:[-*]|\d+[.)])[ \t]*(?:\*\*)?\[P\d\]", re.M | re.I)
# an indented [Pn] bullet with a dash after its tag, or ending in a path:line, is a header that did not parse,
# so it withholds the block; body text (a mid-sentence path, a numbered time) passes, and a dash in it fails safe.
_HEADER_TAIL = re.compile(r"\[P\d\].*?(?:—| [-] )|[\w./-]*[./][\w./-]*:\d+(?:-\d+)?\W*[ \t\r]*$", re.I)
_FINDING_MARK = re.compile(_BULLET_MARK.pattern + r"|^[ \t]*(?:\*\*)?\[P\d\]", re.M | re.I)


def codex_review_findings(text: str, workdir: str | None = None) -> list[dict[str, Any]]:
    """Findings of Codex's native review text: ``- [P1] title — /abs/path:12-14`` then an indented body."""
    out = []
    for m, body_lines, _ in _finding_spans(text):
        first = int(m.group(4))
        if m.group(1) not in _SEVERITY:
            log.warning("unknown priority %s in a review finding; its severity is left unset", m.group(1))
        out.append({"file": relative_path(m.group(3), workdir), "line": first,
                    "end_line": int(m.group(5)) if m.group(5) else first,
                    "severity": _SEVERITY.get(m.group(1)), "priority": m.group(1),
                    "rule": m.group(2).strip(), "message": " ".join(body_lines)})
    return out


def _finding_spans(text: str) -> list[tuple[re.Match[str], list[str], int]]:
    """Each parsed header, its body lines, and where its body ends in ``text``."""
    found = list(_CODEX_FINDING.finditer(text))
    out = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body_lines: list[str] = []
        pos = m.end()
        rest = text[m.end():end]
        # split on \n only, as the header regexes do: str.splitlines also breaks at U+2028, \x0b, \x85 and more
        lines = re.findall(r"[^\n]*\n|[^\n]+", rest)
        pos += len(lines[0]) if lines else 0  # [0] is the rest of the header line
        for line in lines[1:]:
            if line.strip():  # a blank line may separate paragraphs of the body
                if not line[0].isspace():  # the body is indented; a flush line is prose after the findings
                    break
                # a drifted header, not a body line: a bare [Pn] at any indent, or a bullet with a header tail
                if _FINDING_MARK.match(line) and (not _BULLET_MARK.match(line) or _HEADER_TAIL.search(line)):
                    break
                body_lines.append(line.strip())
            pos += len(line)
        out.append((m, body_lines, pos))
    return out


def _unparsed_marks(text: str, rx: re.Pattern[str] = _FINDING_MARK) -> int:
    """Finding markers outside the parsed findings and their bodies (a ``[Pn]`` bullet in a body is prose)."""
    rest, last = [], 0
    for m, _, end in _finding_spans(text):
        rest.append(text[last:m.start()])
        last = end
    rest.append(text[last:])
    return len(rx.findall("".join(rest)))


_CONVERTERS: dict[str, Callable[[str, str | None], list[dict[str, Any]]]] = {
    "codex-review": codex_review_findings,
}


def convert_answer(fmt: str | None, answer: str, workdir: str | None = None, *, failed: bool = False,
                   messages: Sequence[str] = ()) -> str:
    """``answer`` with the findings block of ``fmt`` appended; unchanged when ``fmt`` is ``None``.

    ``messages`` are every agent message of the run, in order: a review may put its findings in an
    earlier message than the last one, so the block is built from all of them (``answer`` is the last).
    Without ``messages`` only ``answer`` is read.

    A ``failed`` run or an empty answer stays as it is: a missing review, not a clean one. So does
    an answer with more finding markers than findings parsed: no block is appended, so a grader
    does not read a review that reported a problem as a clean one.
    """
    texts = list(messages) or [answer]
    if fmt is None or failed or not any(t.strip() for t in texts):
        return answer
    if fmt not in _CONVERTERS:
        raise ValueError(f"unknown answer format {fmt!r} (known: {', '.join(ANSWER_FORMATS)})")
    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    unparsed = 0
    for i, t in enumerate(texts):
        parsed = _CONVERTERS[fmt](t, workdir)
        # every marker counts in the last message and in a message that holds a finding; in an earlier
        # message with none, only a [Pn] bullet does: a bare "[P1] means a blocker" line is narration
        full = parsed or i == len(texts) - 1
        unparsed += _unparsed_marks(t, _FINDING_MARK if full else _BULLET_MARK)
        for f in parsed:
            # a finding repeated (same file, line and title) counts once, as its last version: a later
            # message may revise its priority, range or body, and the grader counts severe findings
            found[(f["file"], f["line"], f["rule"])] = f
    findings = list(found.values())
    if unparsed:  # format drift must not read as a clean review
        log.warning("%s: %d finding marker(s) in the answer but %d parsed; no findings block", fmt,
                    len(findings) + unparsed, len(findings))
        return answer
    if not findings and not answer.strip():  # an empty final answer is a missing review, as with one message
        return answer
    return f"{answer}\n\n```json\n{json.dumps(findings, indent=1)}\n```\n"
