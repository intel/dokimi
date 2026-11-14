# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_test_narrowing.py — the gate on narrowing an existing test."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_test_narrowing.py"
_spec = importlib.util.spec_from_file_location("check_test_narrowing", _SCRIPT)
assert _spec and _spec.loader
ctn = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ctn
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
    ("rel=1e-9", "rel=None", "loosens a tolerance"),
    ("(0.3, rel=1e-9)", "(0.3)", "loosens a tolerance"),
    ("class TestGroup", "@pytest.mark.skip(reason='later')\nclass TestGroup", "adds a skip or xfail"),
    ("[1, 2, 3])", "[1, pytest.param(2, marks=pytest.mark.skip), 3])", "adds a skip or xfail"),
    ("[1, 2, 3])", "[1, pytest.param(2, marks=pytest.mark.xfail), 3])", "adds a skip or xfail"),
    ("def test_raises():\n", "def test_raises():\n    pytest.importorskip('no_such')\n", "adds a skip or xfail"),
])
def test_each_planted_narrowing_is_named(old, new, what):
    found = ctn.compare(BASE, BASE.replace(old, new, 1))
    assert len(found) == 1
    assert what in found[0][2]


STACKED = """
import pytest


@pytest.mark.parametrize("a", [1, 2, 3])
@pytest.mark.parametrize("b", [1, 2, 3])
def test_grid(a, b):
    assert a + b > 0
"""


def test_a_module_level_pytestmark_skip_names_every_test():
    head = BASE.replace("import pytest\n", "import pytest\n\npytestmark = pytest.mark.skip\n", 1)
    assert [(name, why) for name, _, why in ctn.compare(BASE, head)] == [
        (name, "adds a skip or xfail")
        for name in ("test_values", "test_raises", "test_close", "TestGroup.test_member")]


@pytest.mark.parametrize(("before", "after"), [
    ("", "pytest.skip('later', allow_module_level=True)\n"),
    ("", "pytest.importorskip('no_such')\n"),
    ("", "pytestmark: list = [pytest.mark.skip]\n"),
    ("pytestmark = []\n", "pytestmark = []\npytestmark += [pytest.mark.skip]\n"),
    ("MARK = pytest.mark.skipif(False, reason='x')\npytestmark = MARK\n",
     "MARK = pytest.mark.skipif(True, reason='x')\npytestmark = MARK\n"),
])
def test_a_skip_at_the_top_of_a_module_names_every_test(before, after):
    base = BASE.replace("import pytest\n", "import pytest\n" + before, 1)
    head = BASE.replace("import pytest\n", "import pytest\n" + after, 1)
    assert [(name, why) for name, _, why in ctn.compare(base, head)] == [
        (name, "adds a skip or xfail")
        for name in ("test_values", "test_raises", "test_close", "TestGroup.test_member")]


@pytest.mark.parametrize("added", [
    "SLOW = pytest.mark.skipif(not RUN_SLOW, reason='slow')\n",
    "LATER = pytest.mark.skip(reason='later')\n",
])
def test_a_new_mark_constant_skips_no_existing_test(added):
    assert ctn.compare(BASE, BASE.replace("import pytest\n", "import pytest\n" + added, 1)) == []


def test_a_module_imported_with_importorskip_adds_no_skip_where_it_is_used():
    base = BASE.replace("import pytest\n", "import pytest\nnp = pytest.importorskip('numpy')\n", 1)
    base = base.replace("    assert x < 10\n", "    assert x < 10\n    np.array([x])\n")
    assert ctn.compare(base, base.replace("    np.array([x])\n", "    np.array([x])\n    np.zeros(x)\n")) == []


@pytest.mark.parametrize("mark", ["skip", "xfail"])
def test_a_bare_mark_in_pytest_param_is_a_skip(mark):
    head = BASE.replace("[1, 2, 3])", f"[1, pytest.param(2, marks=pytest.mark.{mark}), 3])")
    assert [why for _, _, why in ctn.compare(BASE, head)] == ["adds a skip or xfail"]


def test_stacked_parametrize_cases_are_a_product():
    head = STACKED.replace('"a", [1, 2, 3]', '"a", [1, 2]').replace('"b", [1, 2, 3]', '"b", [1, 2, 3, 4]')
    assert "cuts parametrize cases (9 -> 8)" in ctn.compare(STACKED, head)[0][2]


def test_parametrize_values_in_a_keyword_are_counted():
    base = BASE.replace('"x", [1, 2, 3]', '"x", argvalues=[1, 2, 3]')
    assert "cuts parametrize cases (3 -> 2)" in ctn.compare(base, base.replace("[1, 2, 3]", "[1, 2]"))[0][2]


def test_a_parametrize_list_moved_to_a_constant_is_not_a_cut():
    head = BASE.replace('[1, 2, 3])', 'CASES)').replace("import pytest\n", "import pytest\n\nCASES = [1, 2, 3]\n", 1)
    assert ctn.compare(BASE, head) == []
    cut = head.replace("[1, 2, 3]\n", "[1, 2]\n")
    assert "cuts parametrize cases (3 -> 2)" in ctn.compare(BASE, cut)[0][2]


def test_a_tolerance_in_a_constant_is_read():
    base = BASE.replace("rel=1e-9", "rel=TOL").replace("import pytest\n", "import pytest\n\nTOL = 1e-9\n", 1)
    assert "loosens a tolerance" in ctn.compare(base, base.replace("TOL = 1e-9", "TOL = 1e-1"))[0][2]


def test_a_raised_tolerance_beside_an_unchanged_larger_one_is_named():
    base = BASE.replace("rel=1e-9", "rel=1e-3, abs=1e-9")
    assert "loosens a tolerance" in ctn.compare(base, base.replace("abs=1e-9", "abs=1e-4"))[0][2]


TWO_CLOSE = ("import pytest\n\n\ndef test_close():\n    assert f() == pytest.approx(1, rel=1e-9)\n"
             "    assert g() == pytest.approx(1, rel=1e-3)\n")


def test_a_raised_tolerance_below_another_calls_is_named():
    assert ctn.compare(TWO_CLOSE, TWO_CLOSE.replace("rel=1e-9", "rel=1e-6"))[0][2] == "loosens a tolerance"


def test_reordered_tolerance_checks_are_not_loosened():
    first, second = TWO_CLOSE.split("\n")[4:6]
    assert ctn.compare(TWO_CLOSE, TWO_CLOSE.replace(f"{first}\n{second}", f"{second}\n{first}")) == []


@pytest.mark.parametrize("where", ["after", "before"])
def test_a_new_check_with_a_tolerance_is_not_a_loosened_one(where):
    old = "    assert 0.1 + 0.2 == pytest.approx(0.3, rel=1e-9)\n"
    new = "    assert 2.0 == pytest.approx(2.0, abs=1e-3)\n"
    assert ctn.compare(BASE, BASE.replace(old, old + new if where == "after" else new + old)) == []


def test_a_new_case_with_a_tolerance_is_not_a_loosened_one():
    base = BASE.replace('"x", [1, 2, 3]', '"x", [1, 2]')
    assert ctn.compare(base, base.replace('[1, 2])', '[1, 2, pytest.approx(3, abs=0.5)])')) == []


def test_an_equality_rewritten_as_a_loose_approx_is_loosened():
    base = "import pytest\n\n\ndef test_close():\n    assert f() == 0.3\n"
    head = base.replace("== 0.3", "== pytest.approx(0.3, abs=1e-3)")
    assert ctn.compare(base, head)[0][2] == "loosens a tolerance"


def test_a_reassigned_constant_is_not_inlined_into_a_cycle():
    base = BASE.replace('[1, 2, 3])', 'CASES)').replace("import pytest\n", "import pytest\n\nCASES = [1, 2, 3]\n", 1)
    head = base.replace("@pytest.mark.parametrize", "CASES = CASES[:2]\n\n@pytest.mark.parametrize", 1)
    assert ctn.compare(base, head) == []


def test_a_removed_tolerance_that_restores_a_stricter_default_is_not_loosened():
    base = BASE.replace("rel=1e-9", "rel=1e-3")
    assert ctn.compare(base, base.replace("(0.3, rel=1e-3)", "(0.3)")) == []
    assert ctn.compare(BASE, BASE.replace("(0.3, rel=1e-9)", "(0.3)"))[0][2] == "loosens a tolerance"


def test_a_local_that_shadows_a_module_constant_is_not_read_as_it():
    base = ("import pytest\n\nTOL = 1e-9\n\n\ndef test_close():\n    TOL = 1e-12\n"
            "    assert 1.0 == pytest.approx(1.0, rel=TOL)\n")
    assert ctn.compare(base, base.replace("TOL = 1e-9", "TOL = 1e-1")) == []


@pytest.mark.parametrize("condition", ["", "condition="])
def test_skipif_turned_on_is_a_skip(condition):
    base = BASE.replace("def test_close", f"@pytest.mark.skipif({condition}False, reason='x')\ndef test_close")
    head = base.replace(f"skipif({condition}False", f"skipif({condition}True")
    assert ctn.compare(base, head)[0][2] == "adds a skip or xfail"


def test_a_testcase_class_without_the_test_prefix_is_read():
    base = "import unittest\n\n\nclass Group(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 1)\n"
    assert ctn.compare(base, base.replace("self.assertEqual(1, 1)", "pass"))[0][2] == "fewer checks (1 -> 0)"


def test_a_waiver_string_is_not_a_waiver_comment():
    head = BASE.replace("    assert x < 10\n", "    message = '# Test-narrowed: replaced'\n")
    assert [name for name, _, _ in ctn.compare(BASE, head)] == ["test_values"]


def test_a_waiver_comment_also_present_in_another_base_test_still_waives():
    base = BASE.replace("rel=1e-9)\n", "rel=1e-9)\n    # Test-narrowed: covered elsewhere\n")
    head = base.replace("    assert x < 10\n", "    # Test-narrowed: covered elsewhere\n")
    assert ctn.compare(base, head) == []


MARKED = ("import pytest\n\nMARK = pytest.mark.skipif(False, reason='x')\n\n\ndef helper():\n    pass\n\n\n"
          "@MARK\ndef test_a():\n    assert 1\n    assert 2\n")


def test_a_comment_above_the_mark_constant_does_not_waive_the_test_that_uses_it():
    head = MARKED.replace("def helper", "# Test-narrowed: helper cleanup\ndef helper").replace("    assert 2\n", "")
    assert ctn.compare(MARKED, head) == [("test_a", 11, "fewer checks (2 -> 1)")]


def test_a_dropped_single_case_parametrize_is_not_a_cut():
    base = BASE.replace('"x", [1, 2, 3]', '"x", [1]')
    head = base.replace('@pytest.mark.parametrize("x", [1])\ndef test_values(x):', "def test_values(x=1):")
    assert ctn.compare(base, head) == []
    dropped = BASE.replace('@pytest.mark.parametrize("x", [1, 2, 3])\ndef test_values(x):', "def test_values(x=1):")
    assert ctn.compare(BASE, dropped)[0][2] == "cuts parametrize cases (3 -> 1)"


def test_a_test_moved_into_a_skipped_module_is_named():
    skipped = ctn.tests_in(BASE.replace("import pytest\n", "import pytest\npytestmark = pytest.mark.skip\n"))
    head = BASE.replace("def test_close", "def helper_close")
    found = ctn.compare(BASE, head, {skipped["test_close"].body: [skipped["test_close"]]})
    assert found == [("test_close", 0, "moved, and adds a skip or xfail")]


def test_a_removed_test_is_not_moved_by_a_surviving_twin():
    base = "def test_a(x='ok'):\n    assert x\n\n\ndef test_b(x='ok'):\n    assert x\n"
    head = "def test_b(x='ok'):\n    assert x\n"
    assert ctn.compare(base, head) == [("test_a", 0, "removed")]


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


def test_an_inline_waiver_already_at_base_does_not_waive_a_new_narrowing():
    base = BASE.replace("rel=1e-9)\n", "rel=1e-9)\n    # Test-narrowed: one case is enough\n")
    assert [name for name, _, _ in ctn.compare(base, base.replace("rel=1e-9", "rel=1e-3"))] == ["test_close"]


def test_a_renamed_or_moved_test_is_not_removed():
    shape = ctn.tests_in(BASE)["test_close"]
    head = BASE.replace("def test_close", "def test_sum_is_close")
    assert ctn.compare(BASE, head, {shape.body: [shape]}) == []
    assert ctn.compare(BASE, head) == [("test_close", 0, "removed")]


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


@pytest.mark.parametrize(("path", "read"), [
    ("dokimi/tests/conftest.py", True),
    ("dokimi/conftest.py", True),
    ("dokimi/tests/fixtures/task_formats/x/conftest.py", False),
])
def test_which_conftests_are_read(path, read):
    assert ctn.is_conftest(path) is read


CONFTEST = "import pytest\n\ncollect_ignore_glob = ['fixtures/*']\n"


@pytest.mark.parametrize(("added", "name"), [
    ("collect_ignore = ['test_a.py']\n", "collect_ignore"),
    ("if True:\n    collect_ignore_glob.append('test_*.py')\n", "collect_ignore_glob"),
    ("def pytest_ignore_collect(collection_path):\n    return True\n", "pytest_ignore_collect"),
])
def test_a_conftest_collection_ignore_the_pr_adds_is_named(added, name):
    assert ctn.new_ignores(CONFTEST, CONFTEST + added) == [(name, 4, "may stop collecting tests")]


def test_a_conftest_change_that_ignores_nothing_new_passes():
    head = "# reworded\n" + CONFTEST + "\n\ndef pytest_configure(config):\n    pass\n"
    assert ctn.new_ignores(CONFTEST, head) == []


OTHER = "def test_close():\n    assert round(0.1 + 0.2, 9) == 0.3\n"


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _repo(path: Path) -> None:
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        _git(path, *args)
    (path / "tests").mkdir()
    (path / "tests" / "test_a.py").write_text(BASE, encoding="utf-8")
    (path / "tests" / "test_b.py").write_text(OTHER, encoding="utf-8")
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


def test_a_commit_waiver_covers_only_the_files_that_commit_touches(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    (tmp_path / "tests" / "test_b.py").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "drop b\n\nTest-narrowed: test_b duplicated test_a")
    (tmp_path / "tests" / "test_a.py").write_text(BASE.replace("    assert x < 10\n", ""), encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "trim a")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.main(["--base", "base", "--head", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "tests/test_b.py::test_close narrowed, waived by a commit: test_b duplicated test_a" in out
    assert "::error file=tests/test_a.py,line=5," in out
    assert "::error file=tests/test_b.py" not in out


def test_main_passes_a_test_moved_to_a_new_file(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "tests" / "test_b.py").rename(tmp_path / "tests" / "test_moved.py")
    (tmp_path / "tests" / "test_moved.py").write_text(OTHER + "\n\ndef test_extra():\n    assert 1\n" * 30,
                                                     encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.findings("base", "HEAD") == []


def test_main_names_a_test_moved_into_a_skipped_file(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "tests" / "test_b.py").write_text("def test_keep():\n    assert 2\n", encoding="utf-8")
    (tmp_path / "tests" / "test_moved.py").write_text("import pytest\npytestmark = pytest.mark.skip\n" + OTHER,
                                                     encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "move")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.findings("base", "HEAD") == [("tests/test_b.py", "test_close", 0, "moved, and adds a skip or xfail")]


def test_main_names_a_collection_ignore_added_to_a_conftest(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "tests" / "conftest.py").write_text("collect_ignore = ['test_b.py']\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "ignore b")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.findings("base", "HEAD") == [("tests/conftest.py", "collect_ignore", 1, "may stop collecting tests")]


def test_main_passes_a_rename_and_exits_2_for_a_bad_base(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    _git(tmp_path, "mv", "tests/test_b.py", "tests/test_c.py")
    _git(tmp_path, "commit", "-q", "-m", "rename")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.main(["--base", "base", "--head", "HEAD"]) == 0
    assert ctn.main(["--base", "no-such-ref", "--head", "HEAD"]) == 2
    assert "no-such-ref" in capsys.readouterr().err


def test_a_test_file_renamed_out_of_collection_is_removed(tmp_path, monkeypatch):
    _repo(tmp_path)
    _git(tmp_path, "mv", "tests/test_b.py", "tests/helper_b.py")
    _git(tmp_path, "commit", "-q", "-m", "rename")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.findings("base", "HEAD") == [("tests/helper_b.py", "test_close", 0, "removed")]


def test_a_test_file_replaced_by_a_symlink_is_removed(tmp_path, monkeypatch):
    _repo(tmp_path)
    (tmp_path / "tests" / "test_b.py").unlink()
    (tmp_path / "tests" / "test_b.py").symlink_to("test_a.py")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "link")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.findings("base", "HEAD") == [("tests/test_b.py", "test_close", 0, "removed")]


def test_a_commit_waiver_covers_a_non_ascii_path(tmp_path, monkeypatch):
    _repo(tmp_path)
    _git(tmp_path, "mv", "tests/test_b.py", "tests/test_café.py")
    (tmp_path / "tests" / "test_café.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "trim\n\nTest-narrowed: gone")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert ctn.waivers("base", "HEAD") == {"tests/test_café.py": "gone", "tests/test_b.py": "gone"}


def test_a_commit_waiver_on_a_merge_covers_its_files(tmp_path, monkeypatch):
    _repo(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "side")
    (tmp_path / "tests" / "test_c.py").write_text("def test_c():\n    assert 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "side")
    _git(tmp_path, "checkout", "-q", "-")
    (tmp_path / "tests" / "test_b.py").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "drop b")
    _git(tmp_path, "merge", "-q", "--no-ff", "side", "-m", "merge\n\nTest-narrowed: gone")
    monkeypatch.setattr(ctn, "REPO", tmp_path)
    assert "tests/test_c.py" in ctn.waivers("base", "HEAD")
