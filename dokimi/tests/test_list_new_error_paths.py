# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/list_new_error_paths.py — the reviewer's list of new error paths."""

from __future__ import annotations

import importlib.util
import subprocess

import pytest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "list_new_error_paths.py"
_spec = importlib.util.spec_from_file_location("list_new_error_paths", _SCRIPT)
assert _spec and _spec.loader
lnep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lnep)

DIFF = """\
diff --git a/dokimi/dokimi/x.py b/dokimi/dokimi/x.py
--- a/dokimi/dokimi/x.py
+++ b/dokimi/dokimi/x.py
@@ -10,0 +11,4 @@ def f():
+    if bad:
+        raise ValueError("bad")
+    sys.exit(2)
+    return ExitStatus(exit_code=1)
@@ -40 +44,2 @@
-    raise OldError
+    message = "raise is a word here"
+def test_bad_input_fails():
diff --git a/dokimi/docs/x.md b/dokimi/docs/x.md
--- a/dokimi/docs/x.md
+++ b/dokimi/docs/x.md
@@ -1,0 +2 @@
+raise ValueError in prose
"""


def test_lists_added_raise_exit_and_test_lines_with_their_new_line_numbers():
    assert lnep.added_sites(DIFF) == [
        ("dokimi/dokimi/x.py", 12, "raise", 'raise ValueError("bad")'),
        ("dokimi/dokimi/x.py", 13, "exit", "sys.exit(2)"),
        ("dokimi/dokimi/x.py", 14, "exit", "return ExitStatus(exit_code=1)"),
        ("dokimi/dokimi/x.py", 45, "test", "def test_bad_input_fails():"),
    ]


def test_a_deleted_file_lists_nothing():
    diff = "--- a/dokimi/dokimi/x.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-raise X\n"
    assert lnep.added_sites(diff) == []


def test_main_lists_the_sites_of_a_real_diff(monkeypatch, capsys):
    result = lnep.subprocess.CompletedProcess(args=[], returncode=0, stdout=DIFF, stderr="")
    monkeypatch.setattr(lnep.subprocess, "run", lambda *args, **kwargs: result)
    assert lnep.main(["--base", "HEAD~1", "--head", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "4 new error path(s)" in out
    assert 'dokimi/dokimi/x.py:12: raise: raise ValueError("bad")' in out
    assert "dokimi/dokimi/x.py:45: test: def test_bad_input_fails():" in out


def test_main_returns_2_when_git_cannot_diff(capsys):
    assert lnep.main(["--base", "no-such-ref-xyz", "--head", "HEAD"]) == 2


def test_an_added_line_starting_with_two_pluses_is_not_a_file_header():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+++ y\n+if bad: raise Z\n"
    assert lnep.added_sites(diff) == [("x.py", 2, "raise", "if bad: raise Z")]


def test_a_removed_and_added_line_that_look_like_file_headers_do_not_hide_later_sites():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -3 +3 @@\n--- old\n+++ new\n@@ -9,0 +10 @@\n+    raise Z\n"
    assert lnep.added_sites(diff) == [("x.py", 10, "raise", "raise Z")]


def test_a_path_with_spaces_keeps_its_sites():
    diff = "--- a/my x.py\t\n+++ b/my x.py\t\n@@ -0,0 +1 @@\n+raise Z\n"
    assert lnep.added_sites(diff) == [("my x.py", 1, "raise", "raise Z")]


def test_a_context_row_inside_a_fused_hunk_is_counted():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n-raise OldA\n+raise NewA\n unchanged()\n-raise OldB\n+raise NewB\n"
    assert lnep.added_sites(diff) == [("x.py", 1, "raise", "raise NewA"), ("x.py", 3, "raise", "raise NewB")]


def test_an_exit_call_with_a_space_before_the_paren_is_listed():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+sys.exit (2)\n+SystemExit (2)\n"
    assert [s[2] for s in lnep.added_sites(diff)] == ["exit", "exit"]


def test_main_passes_explicit_prefixes_and_no_fused_hunks(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return lnep.subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lnep.subprocess, "run", fake_run)
    assert lnep.main([]) == 0
    assert {"--src-prefix=a/", "--dst-prefix=b/", "--inter-hunk-context=0"} <= set(seen[0])


def _external_diff(repo, monkeypatch):
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "true")


def _textconv(repo, monkeypatch):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("*.py diff=up\n", encoding="utf-8")
    subprocess.run(["git", "config", "diff.up.textconv", "tr a-z A-Z <"], cwd=repo, check=True)


@pytest.mark.parametrize("configure", [_external_diff, _textconv])
def test_main_lists_sites_whatever_the_user_diff_config(tmp_path, monkeypatch, capsys, configure):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "x.py").write_text("raise ValueError(1)\n", encoding="utf-8")
    git("add", "x.py")
    git("commit", "-q", "-m", "add")
    monkeypatch.setattr(lnep, "REPO", tmp_path)
    configure(tmp_path, monkeypatch)
    assert lnep.main(["--base", "HEAD~1", "--head", "HEAD"]) == 0
    assert "x.py:1: raise: raise ValueError(1)" in capsys.readouterr().out
