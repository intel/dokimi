# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/list_new_error_paths.py — the reviewer's list of new error paths."""

from __future__ import annotations

import importlib.util
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


def test_main_lists_the_sites_of_a_real_diff(capsys):
    assert lnep.main(["--base", "HEAD", "--head", "HEAD"]) == 0
    assert capsys.readouterr().out.startswith("0 new error path(s)")


def test_main_returns_2_when_git_cannot_diff(capsys):
    assert lnep.main(["--base", "no-such-ref-xyz", "--head", "HEAD"]) == 2
