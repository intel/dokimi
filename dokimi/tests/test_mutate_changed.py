# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/mutate_changed.py — mutation of a PR's changed lines."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mutate_changed.py"
_spec = importlib.util.spec_from_file_location("mutate_changed", _SCRIPT)
assert _spec and _spec.loader
mc = importlib.util.module_from_spec(_spec)
sys.modules["mutate_changed"] = mc
_spec.loader.exec_module(mc)

SRC = '''def f(a, b):
    """Doc: a == b."""
    if a == b and a > 0:
        return a + 1
    return not b
'''


def _ops(lines: set[int]) -> list[tuple[int, str, str]]:
    return [(m.line, m.op, mc.apply(SRC, m)) for m in mc.mutants_for("x.py", SRC, lines)]


def test_mutants_only_on_changed_lines_and_each_parses() -> None:
    got = _ops({3})
    assert {op for _, op, _ in got} == {"flip-compare", "swap-boolop", "negate-condition", "bump-int"}
    assert all(line == 3 for line, _, _ in got)
    for _, _, text in got:
        ast.parse(text)
        assert text.splitlines()[3:] == SRC.splitlines()[3:]  # lines after the mutant untouched
    texts = {(op, t.splitlines()[2]) for _, op, t in got}
    assert texts == {
        ("flip-compare", "    if a != b and a > 0:"),
        ("flip-compare", "    if a == b and a <= 0:"),
        ("swap-boolop", "    if a == b or a > 0:"),
        ("negate-condition", "    if not (a == b and a > 0):"),
        ("bump-int", "    if a == b and a > 1:"),
    }


def test_return_addsub_and_not_mutants() -> None:
    got = {(line, op, t.splitlines()[line - 1].strip()) for line, op, t in _ops({4, 5})}
    assert got == {
        (4, "return-none", "return None"),
        (4, "swap-addsub", "return a - 1"),
        (4, "bump-int", "return a + 2"),
        (5, "return-none", "return None"),
        (5, "drop-not", "return (b)"),
    }


def test_docstring_lines_are_not_mutated() -> None:
    assert _ops({2}) == []


def test_string_concatenation_is_not_swapped() -> None:
    assert mc.mutants_for("x.py", "y = 'a' + b\n", {1}) == []


def test_apply_uses_utf8_byte_columns() -> None:
    src = "x = 'é' if a < b else 0\n"
    (m,) = [m for m in mc.mutants_for("x.py", src, {1}) if m.op == "flip-compare"]
    assert mc.apply(src, m) == "x = 'é' if a >= b else 0\n"


def test_pick_spreads_the_cap_over_lines() -> None:
    ms = mc.mutants_for("x.py", SRC, {3, 4, 5})
    chosen = mc.pick(ms, 3)
    assert [m.line for m in chosen] == [3, 4, 5]
    assert len(mc.pick(ms, 100)) == len(ms)


def test_candidate_tests_match_dotted_and_from_imports(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text("from dokimi.subjects import scorecard\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("import dokimi.subjects.scorecard as s\n", encoding="utf-8")
    (tmp_path / "test_c.py").write_text("from dokimi.subjects import scorecards\n", encoding="utf-8")
    assert mc.candidate_tests(["dokimi/dokimi/subjects/scorecard.py"], tmp_path) == ["tests/test_a.py", "tests/test_b.py"]
    (tmp_path / "test_scorecard_x.py").write_text("from dokimi.subjects import scorecard\n", encoding="utf-8")
    assert mc.candidate_tests(["dokimi/dokimi/subjects/scorecard.py"], tmp_path)[0] == "tests/test_scorecard_x.py"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_changed_lines_reads_added_and_edited_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (pkg / "m.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (pkg / "m.py").write_text("a = 1\nb = 5\nc = 3\nd = 4\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "head")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    assert mc.changed_lines("HEAD~1") == {"dokimi/dokimi/m.py": {2, 4}}
    with pytest.raises(SystemExit, match="git diff"):
        mc.changed_lines("no-such-ref")


def test_markdown_lists_survivors_and_uncovered() -> None:
    m = mc.Mutant("dokimi/dokimi/x.py", 3, "flip-compare", (3, 4), (3, 10), "a != b", "a == b", status="survived", tests=["t"])
    k = mc.Mutant("dokimi/dokimi/x.py", 4, "return-none", (4, 8), (4, 9), "None", "a", status="killed")
    text = mc.markdown({"mutants": [vars(m), vars(k)], "mutants_covered": 2, "max_mutants": 40,
                        "uncovered_lines": {"dokimi/dokimi/x.py": [7]}, "seconds": 1.0})
    assert "1 killed" in text and "**1 survived**" in text
    assert "| `dokimi/dokimi/x.py:3` | flip-compare | `a == b` | `a != b` | 1 |" in text
    assert "`dokimi/dokimi/x.py`: 7" in text
