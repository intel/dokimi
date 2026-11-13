# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_test_narrowing.py — the gate on narrowing an existing test."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_test_narrowing.py"
_spec = importlib.util.spec_from_file_location("check_test_narrowing", _SCRIPT)
assert _spec and _spec.loader
ctn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctn)

BASE = '''
import pytest


@pytest.mark.parametrize("x", [1, 2, 3])
def test_values(x):
    assert x > 0
    assert x < 10


def test_raises():
    with pytest.raises(ValueError, match="bad"):
        int("bad")


def test_close():
    assert 0.1 + 0.2 == pytest.approx(0.3, rel=1e-9)


class TestGroup:
    def test_member(self):
        self.assertEqual(1, 1)
'''


@pytest.mark.parametrize(("old", "new", "what"), [
    ("    assert x < 10\n", "", "fewer checks (2 -> 1)"),
    ('[1, 2, 3])', '[1, 2])', "cuts parametrize cases (3 -> 2)"),
    ('ValueError, match="bad"', 'ValueError', "drops a match= from pytest.raises"),
    ('ValueError, match="bad"', 'Exception, match="bad"', "widens pytest.raises to Exception"),
    ("rel=1e-9", "rel=1e-3", "loosens a tolerance"),
    ("def test_close", "@pytest.mark.skip\ndef test_close", "adds a skip or xfail"),
    ("def test_raises():\n", "def test_raises():\n    pytest.xfail('later')\n", "adds a skip or xfail"),
    ("        self.assertEqual(1, 1)\n", "        pass\n", "fewer checks (1 -> 0)"),
])
def test_each_planted_narrowing_is_named(old, new, what):
    found = ctn.compare(BASE, BASE.replace(old, new, 1))
    assert len(found) == 1
    assert what in found[0][2]


def test_a_removed_test_and_a_deleted_file_are_reported():
    head = BASE.replace("def test_close", "def helper_close")
    assert ctn.compare(BASE, head) == [("test_close", 0, "removed")]
    assert {name for name, _, _ in ctn.compare(BASE, None)} == {
        "test_values", "test_raises", "test_close", "TestGroup.test_member"}


@pytest.mark.parametrize(("old", "new"), [
    ("    assert x < 10\n", "    assert x < 10\n    assert x != 5\n"),  # one more check
    ("rel=1e-9", "rel=1e-12"),                                      # tighter tolerance
    ('[1, 2, 3])', '[1, 2, 3, 4])'),                                # one more case
    ("int(\"bad\")", "int(\"bad\")  # reworded"),                  # no change in checks
])
def test_widening_or_keeping_a_test_passes(old, new):
    assert ctn.compare(BASE, BASE.replace(old, new, 1)) == []


def test_an_inline_waiver_inside_the_test_waives_only_that_test():
    head = BASE.replace("    assert x < 10\n", "    # Test-narrowed: x < 10 is test_bounds's job\n")
    head = head.replace("rel=1e-9", "rel=1e-3")
    assert [name for name, _, _ in ctn.compare(BASE, head)] == ["test_close"]


def test_a_head_that_does_not_parse_is_left_to_the_test_run():
    assert ctn.compare(BASE, "def test_values(:\n") == []


@pytest.mark.parametrize(("path", "read"), [
    ("dokimi/tests/test_cli.py", True),
    ("dokimi/tests/repos/cli_test.py", True),
    ("dokimi/tests/conftest.py", False),
    ("dokimi/tests/fixtures/task_formats/x/tests/test_outputs.py", False),
    ("dokimi/dokimi/test_utils.py", False),
])
def test_which_files_are_read(path, read):
    assert ctn.is_test_file(path) is read


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _repo(path: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        _git(path, *args)
    (path / "tests").mkdir()
    (path / "tests" / "test_a.py").write_text(BASE, encoding="utf-8")
    (path / "tests" / "test_b.py").write_text(BASE, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")
    _git(path, "tag", "base")


def _narrow(path: Path, message: str) -> None:
    (path / "tests" / "test_a.py").write_text(BASE.replace("    assert x < 10\n", ""), encoding="utf-8")
    (path / "tests" / "test_b.py").unlink()
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", message)


def test_main_flags_a_narrowed_and_a_deleted_test_file(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    _narrow(tmp_path, "trim tests")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.main(["--base", "base", "--head", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "::error file=tests/test_a.py,line=5,title=Test narrowed::test_values: fewer checks" in out
    assert "::error file=tests/test_b.py,title=Test narrowed::test_close: removed" in out


def test_main_passes_with_a_commit_waiver_and_prints_its_reason(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    _narrow(tmp_path, "trim tests\n\nTest-narrowed: test_b duplicated test_a")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.main(["--base", "base", "--head", "HEAD"]) == 0
    assert "test_b duplicated test_a" in capsys.readouterr().out


def test_main_passes_a_rename_and_exits_2_for_a_bad_base(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    _git(tmp_path, "mv", "tests/test_b.py", "tests/test_c.py")
    _git(tmp_path, "commit", "-q", "-m", "rename")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.main(["--base", "base", "--head", "HEAD"]) == 0
    assert ctn.main(["--base", "no-such-ref", "--head", "HEAD"]) == 2
    assert "no-such-ref" in capsys.readouterr().err
