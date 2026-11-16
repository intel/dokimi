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
import subprocess
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODES = {"BLE001", "S110", "S112"}
# ruff's extend-exclude in pyproject.toml, plus the virtualenvs a checkout may hold.
SKIP = ("benchmarks/", "data/", "tests/fixtures/task_formats/", ".venv/", "venv/", "build/", ".tox/", "dist/")
_NOQA = re.compile(r"#\s*(?:(?:ruff|flake8)\s*:\s*)?noqa:\s*(?P<codes>[A-Z]+[0-9]+(?:[\s,]+[A-Z]+[0-9]+)*)(?P<rest>.*)$", re.I)
_REASON = re.compile(r"^\s*(?:[-—:(]+\s*)?(?P<text>.*?)\)?\s*$")
# coverage.py's default exclude pattern: the pragma must start with a `#` of its own.
_COVERAGE_PRAGMA = re.compile(r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)")


def unexplained(source: str) -> list[int]:
    """Lines of *source* whose ``noqa`` names a blind-except code but gives no reason."""
    lines = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        m = _NOQA.search(tok.string)
        if not m or not CODES & set(re.split(r"[\s,]+", m.group("codes"))):
            continue
        reason = _REASON.match(_COVERAGE_PRAGMA.sub("", m.group("rest")))
        if not reason or len(reason.group("text").split()) < 2:
            lines.append(tok.start[0])
    return lines


def _python_files() -> list[Path]:
    # The files ruff checks: what git does not hide (.gitignore, so mutants/ is out too).
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / rel for rel in sorted(listed.split("\0"))
            if rel and not rel.startswith(SKIP) and (ROOT / rel).is_file()]


def test_python_files_follow_gitignore_and_skip_tox(tmp_path: Path, monkeypatch) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("mutants/\n", encoding="utf-8")
    for rel in ("a.py", "mutants/scratch.py", ".tox/py311/x.py", "pkg/b.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "gone.py"], check=True)
    (tmp_path / "gone.py").unlink()  # staged, then deleted in the working tree
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in _python_files()] == ["a.py", "pkg/b.py"]


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


def test_upper_case_and_doubled_dashes_are_still_checked() -> None:
    assert unexplained("try:\n    x()\nexcept Exception:  # NOQA: BLE001 -- ok\n    pass\n") == [3]


def test_a_code_after_a_space_separated_one_is_still_checked() -> None:
    assert unexplained("try:\n    x()\nexcept:  # noqa: E722 BLE001 S110\n    pass\n") == [3]
    assert unexplained("try:\n    x()\nexcept:  # noqa: E722 BLE001 S110 - logged by the caller\n    pass\n") == []


def test_a_coverage_pragma_is_not_a_reason() -> None:
    src = "try:\n    x()\nexcept Exception:  # noqa: BLE001, S110 # pragma: no cover\n    pass\n"
    assert unexplained(src) == [3]
    assert unexplained(src.replace("# pragma: no cover", "# pragma: no cover - logged upstream")) == []


def test_a_file_level_noqa_needs_a_reason_too() -> None:
    src = "try:\n    x()\nexcept Exception:\n    pass\n"
    assert unexplained("# ruff: noqa: BLE001, S110\n" + src) == [1]
    assert unexplained("# flake8: noqa: BLE001\n" + src) == [1]
    assert unexplained("# ruff: noqa: BLE001 - every probe failure is logged upstream\n" + src) == []


def test_a_reason_does_not_hide_a_coverage_pragma() -> None:
    bad = [f"{p.relative_to(ROOT).as_posix()}:{n}"
           for p in _python_files() if p.resolve() != Path(__file__).resolve()
           for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if "noqa" in line and "pragma: no cover" in line and not _COVERAGE_PRAGMA.search(line)]
    assert not bad, "a noqa reason swallowed a coverage pragma: " + ", ".join(bad)
