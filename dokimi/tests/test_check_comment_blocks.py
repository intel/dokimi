# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_comment_blocks.py — the comment block length gate."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_comment_blocks.py"
_spec = importlib.util.spec_from_file_location("check_comment_blocks", _SCRIPT)
assert _spec and _spec.loader
ccb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccb)

LONG = "# one\n# two\n# three\nx = 1\n"


@pytest.mark.parametrize(("path", "read"), [
    ("dokimi/dokimi/cli.py", True),
    ("dokimi/Makefile", True),
    (".agent-eval/Dockerfile", True),
    (".github/workflows/ci.yml", True),
    (".pre-commit-config.yaml", True),
    ("dokimi/pyproject.toml", True),
    ("dokimi/docs/README.md", False),        # `#` is a heading there
    ("dokimi/requirements.lock.txt", False),  # generated
    ("dokimi/tests/fixtures/task_formats/x/solve.sh", False),  # third-party copy
])
def test_which_files_are_read(path, read):
    assert ccb.checked(path) is read


def test_a_hash_inside_a_python_string_is_not_a_comment():
    text = 'x = """\n# a\n# b\n# c\n"""\n'
    assert ccb.comment_lines(text, python=True) == set()


def test_a_trailing_comment_does_not_join_a_block():
    text = "# a\n# b\nx = 1  # c\n"
    assert ccb.blocks(ccb.comment_lines(text, python=True)) == [(1, 2)]


def test_shebang_license_and_pragmas_do_not_count():
    text = ("#!/usr/bin/env python\n# Copyright (C) 2026 Intel Corporation\n"
            "# SPDX-License-Identifier: Apache-2.0\n# a\n# b\n# fmt: off\n# c\n")
    assert ccb.blocks(ccb.comment_lines(text, python=True)) == [(4, 5), (7, 7)]


def test_python_that_does_not_tokenize_falls_back_to_lines():
    text = "# a\n# b\n# c\nx = '\n"
    assert ccb.comment_lines(text, python=True) == {1, 2, 3}


def test_yaml_comments_are_read_by_line_including_in_run_scripts():
    text = "steps:\n  - run: |\n      # a\n      # b\n      # c\n      make\n"
    assert ccb.blocks(ccb.comment_lines(text, python=False)) == [(3, 5)]


def test_a_form_feed_keeps_line_numbers_in_step_with_git():
    text = "a: 1\f\n# x\n# y\n# z\n"
    assert ccb.long_blocks(text, "f.yml", {2}) == [(2, 4, 3)]


def test_a_banner_is_one_line_but_rulers_do_not_hide_a_long_block():
    banner = "# ------\n# Section\n# ------\nx = 1\n"
    assert ccb.long_blocks(banner, "f.py", {2}) == []
    ruled = "# ======\n# a\n# b\n# c\n# ======\n"
    assert ccb.long_blocks(ruled, "f.py", {3}) == [(1, 5, 3)]


@pytest.mark.parametrize(("changed", "flagged"), [
    ({2}, [(1, 3, 3)]),   # an edited line inside the block
    ({4}, []),            # only the code below it changed
    (set(), []),          # an existing block nobody touched
])
def test_a_long_block_fails_only_when_the_pr_touches_it(changed, flagged):
    assert ccb.long_blocks(LONG, "f.py", changed) == flagged


def test_two_lines_pass_even_when_both_are_new():
    assert ccb.long_blocks("# one\n# two\nx = 1\n", "f.py", {1, 2}) == []


def _git_repo(path: Path) -> None:
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"],
                ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *cmd], cwd=path, check=True)


def _commit(path: Path, msg: str, tag: str | None = None) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True)
    if tag:
        subprocess.run(["git", "tag", tag], cwd=path, check=True)


def test_main_flags_a_new_long_block_with_its_lines(tmp_path, monkeypatch, capsys):
    _git_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "base", tag="base")
    (tmp_path / "a.py").write_text("x = 1\n" + LONG, encoding="utf-8")
    _commit(tmp_path, "pr")
    monkeypatch.setattr(ccb, "REPO", tmp_path)
    assert ccb.main(["--base", "base", "--head", "HEAD"]) == 1
    assert "::error file=a.py,line=2,endLine=4," in capsys.readouterr().out


def test_main_passes_an_untouched_long_block_and_a_renamed_file(tmp_path, monkeypatch):
    _git_repo(tmp_path)
    (tmp_path / "old.py").write_text(LONG + "y = 2\n" * 10, encoding="utf-8")
    (tmp_path / "b.yml").write_text(LONG, encoding="utf-8")
    _commit(tmp_path, "base", tag="base")
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    (tmp_path / "b.yml").write_text(LONG + "z: 3\n", encoding="utf-8")
    _commit(tmp_path, "move and append")
    monkeypatch.setattr(ccb, "REPO", tmp_path)
    assert ccb.findings("base", "HEAD") == []


def test_main_exits_2_for_a_base_git_cannot_diff(tmp_path, monkeypatch, capsys):
    _git_repo(tmp_path)
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    _commit(tmp_path, "base")
    monkeypatch.setattr(ccb, "REPO", tmp_path)
    assert ccb.main(["--base", "no-such-ref", "--head", "HEAD"]) == 2
    assert "no-such-ref" in capsys.readouterr().err
