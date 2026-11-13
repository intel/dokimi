# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every kept blind ``except`` says why in its ``noqa``.

ruff's BLE001, S110 and S112 fail an ``except Exception`` that neither re-raises nor
logs the traceback, and an ``except`` whose body is only ``pass`` or ``continue``.
A site that must catch everything keeps a ``# noqa: BLE001 - <reason>`` naming what
happens to the failure (logged, recorded on the row, re-asked, answered False). ruff
accepts a bare ``# noqa: BLE001``; this test does not, so the gate cannot be passed
by adding the code alone.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODES = {"BLE001", "S110", "S112"}
# ruff's extend-exclude in pyproject.toml, plus the virtualenvs a checkout may hold.
SKIP = ("benchmarks/", "data/", "tests/fixtures/task_formats/", ".venv/", "venv/", "build/")
_NOQA = re.compile(r"#\s*noqa:\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)(?P<rest>.*)$")
_REASON = re.compile(r"^\s*(?:[-—:(]\s*)?(?P<text>.*?)\)?\s*$")


def unexplained(source: str) -> list[int]:
    """Lines of *source* whose ``noqa`` names a blind-except code but gives no reason."""
    lines = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        m = _NOQA.search(tok.string)
        if not m or not CODES & {c.strip() for c in m.group("codes").split(",")}:
            continue
        reason = _REASON.match(m.group("rest"))
        if not reason or len(reason.group("text").split()) < 2:
            lines.append(tok.start[0])
    return lines


def _python_files() -> list[Path]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith(SKIP):
            out.append(path)
    return out


def test_every_blind_except_noqa_has_a_reason() -> None:
    bad = [f"{p.relative_to(ROOT).as_posix()}:{line}"
           for p in _python_files()
           for line in unexplained(p.read_text(encoding="utf-8"))]
    assert not bad, "a # noqa for BLE001/S110/S112 needs a reason after it: " + ", ".join(bad)


def test_a_bare_code_fails() -> None:
    src = "try:\n    x()\nexcept Exception:  # noqa: BLE001\n    pass\n"
    assert unexplained(src) == [3]


def test_a_code_with_one_word_fails() -> None:
    assert unexplained("try:\n    x()\nexcept Exception:  # noqa: BLE001 - fine\n    pass\n") == [3]


def test_a_reason_passes_in_each_separator_style() -> None:
    for comment in ("# noqa: BLE001 - one bad cell must not end the sweep",
                    "# noqa: BLE001 — a bad frontmatter keeps its dir name",
                    "# noqa: BLE001, S110 (logged and skipped)"):
        assert unexplained(f"try:\n    x()\nexcept Exception:  {comment}\n    pass\n") == []


def test_other_codes_are_not_checked() -> None:
    assert unexplained("import os  # noqa: F401\n") == []
