# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Final-answer conversions a harness profile names in ``answer_format`` (contract §6).

A native review mode answers in its own format, not the ```` ```json ```` block
a review task asks for. The conversion keeps the harness's text and appends
that block, so a grader reading the last ```` ```json ```` block of the answer
reads the native findings; nothing is dropped from what the agent said.

Findings are ``{file, line, end_line, severity, priority, rule, message}``:
``file`` relative to the workspace, ``line`` the first line of the range,
``severity`` ``blocker`` (P0), ``major`` (P1) or ``minor`` (P2, P3), ``rule``
the finding's title and ``message`` its body.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from .transcript import relative_path

log = logging.getLogger(__name__)

ANSWER_FORMATS = ("codex-review",)
_SEVERITY = {"P0": "blocker", "P1": "major", "P2": "minor", "P3": "minor"}
# the title may hold " — ": the path part is what follows the last one and holds none
_CODEX_FINDING = re.compile(r"^- \[(P\d)\] (.+) — ([^\s—][^—]*?):(\d+)(?:-(\d+))?[ \t]*$", re.M)
_FINDING_MARK = re.compile(r"^\s*[-*]?\s*\[P\d\]", re.M | re.I)


def codex_review_findings(text: str, workdir: str | None = None) -> list[dict[str, Any]]:
    """Findings of Codex's native review text: ``- [P1] title — /abs/path:12-14`` then an indented body."""
    found = list(_CODEX_FINDING.finditer(text))
    out = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body_lines = []
        for line in text[m.end():end].splitlines()[1:]:  # [0] is the rest of the header line
            if not line.strip() or not line[0].isspace():  # the body: the indented lines up to a blank or other line
                break
            body_lines.append(line.strip())
        body = " ".join(body_lines)
        first = int(m.group(4))
        out.append({"file": relative_path(m.group(3), workdir), "line": first,
                    "end_line": int(m.group(5)) if m.group(5) else first,
                    "severity": _SEVERITY.get(m.group(1), "minor"), "priority": m.group(1),
                    "rule": m.group(2).strip(), "message": body})
    return out


_CONVERTERS: dict[str, Callable[[str, str | None], list[dict[str, Any]]]] = {
    "codex-review": codex_review_findings,
}


def convert_answer(fmt: str | None, answer: str, workdir: str | None = None) -> str:
    """``answer`` with the findings block of ``fmt`` appended; unchanged when ``fmt`` is ``None``."""
    if fmt is None or not answer.strip():  # an empty answer is a failed or missing run, not a clean review
        return answer
    if fmt not in _CONVERTERS:
        raise ValueError(f"unknown answer format {fmt!r} (known: {', '.join(ANSWER_FORMATS)})")
    findings = _CONVERTERS[fmt](answer, workdir)
    marked = len(_FINDING_MARK.findall(answer))
    if marked > len(findings):  # format drift must not read as a clean review
        log.warning("%s: %d finding marker(s) in the answer but %d parsed", fmt, marked, len(findings))
    return f"{answer}\n\n```json\n{json.dumps(findings, indent=1)}\n```\n"
