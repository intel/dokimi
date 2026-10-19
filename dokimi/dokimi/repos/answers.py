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
import re
from collections.abc import Callable
from typing import Any

from .events import _relative

ANSWER_FORMATS = ("codex-review",)
_SEVERITY = {"P0": "blocker", "P1": "major", "P2": "minor", "P3": "minor"}
_CODEX_FINDING = re.compile(r"^- \[(P\d)\] (.+?) — (\S.*?):(\d+)(?:-(\d+))?\s*$", re.M)


def codex_review_findings(text: str, workdir: str | None = None) -> list[dict[str, Any]]:
    """Findings of Codex's native review text: ``- [P1] title — /abs/path:12-14`` then an indented body."""
    found = list(_CODEX_FINDING.finditer(text))
    out = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body = " ".join(line.strip() for line in text[m.end():end].splitlines() if line.strip())
        first = int(m.group(4))
        out.append({"file": _relative(m.group(3), workdir), "line": first,
                    "end_line": int(m.group(5)) if m.group(5) else first,
                    "severity": _SEVERITY.get(m.group(1), "minor"), "priority": m.group(1),
                    "rule": m.group(2).strip(), "message": body})
    return out


_CONVERTERS: dict[str, Callable[[str, str | None], list[dict[str, Any]]]] = {
    "codex-review": codex_review_findings,
}


def convert_answer(fmt: str | None, answer: str, workdir: str | None = None) -> str:
    """``answer`` with the findings block of ``fmt`` appended; unchanged when ``fmt`` is ``None``."""
    if fmt is None:
        return answer
    if fmt not in _CONVERTERS:
        raise ValueError(f"unknown answer format {fmt!r} (known: {', '.join(ANSWER_FORMATS)})")
    findings = _CONVERTERS[fmt](answer, workdir)
    return f"{answer}\n\n```json\n{json.dumps(findings, indent=1)}\n```\n"
