# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_docs_review_triggers.py, and the repo's own depends_on mappings."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_docs_review_triggers.py"
_spec = importlib.util.spec_from_file_location("check_docs_review_triggers", _SCRIPT)
assert _spec and _spec.loader
cdrt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdrt)

PAGE = "dokimi/docs/novelty.md"
PAGES = {PAGE: ["dokimi/dokimi/novelty/**", "dokimi/dokimi/commands/novelty.py"]}


@pytest.mark.parametrize(("glob", "path", "hit"), [
    ("dokimi/dokimi/novelty/**", "dokimi/dokimi/novelty/probes.py", True),
    ("dokimi/dokimi/novelty/**", "dokimi/dokimi/novelty/sub/deep.py", True),
    ("dokimi/dokimi/novelty/**", "dokimi/dokimi/novelty_gate.py", False),
    ("dokimi/dokimi/eval/*.py", "dokimi/dokimi/eval/stats.py", True),
    ("dokimi/dokimi/eval/*.py", "dokimi/dokimi/eval/sub/stats.py", False),
    ("dokimi/dokimi/eval/stat?.py", "dokimi/dokimi/eval/stats.py", True),
    ("dokimi/dokimi/tool_preference.py", "dokimi/dokimi/tool_preference.py", True),
    ("dokimi/dokimi/tool_preference.py", "dokimi/dokimi/tool_preference.pyc", False),
    ("dokimi/dokimi/a.b", "dokimi/dokimi/aXb", False),  # `.` is literal
])
def test_glob_matching(glob, path, hit):
    assert bool(cdrt.glob_regex(glob).match(path)) is hit


def test_dependency_changed_page_untouched_is_flagged():
    changed = ["dokimi/dokimi/novelty/probes.py", "dokimi/dokimi/cli.py"]
    assert cdrt.triggers(PAGES, changed) == {PAGE: ["dokimi/dokimi/novelty/probes.py"]}


def test_page_touched_in_the_same_pr_is_not_flagged():
    assert cdrt.triggers(PAGES, ["dokimi/dokimi/novelty/probes.py", PAGE]) == {}


def test_unrelated_change_flags_nothing():
    assert cdrt.triggers(PAGES, ["dokimi/dokimi/harnesses/tasks.py"]) == {}


def test_stale_glob_is_reported():
    files = ["dokimi/dokimi/novelty/probes.py"]
    assert cdrt.stale_globs(PAGES, files) == [f"{PAGE}: dokimi/dokimi/commands/novelty.py"]


def test_front_matter_parsing():
    head = "<!--\nCopyright\n-->\n"
    assert cdrt.depends_on("---\ndepends_on: [a/b.py, c/**]\n---\n" + head) == ["a/b.py", "c/**"]
    assert cdrt.depends_on(head + "# Title\n") == []
    assert cdrt.depends_on("---\ntitle: x\n---\n# Title\n") == []
    with pytest.raises(ValueError):
        cdrt.depends_on("---\ndepends_on: a/b.py\n---\n")


def test_main_writes_summary_and_warnings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cdrt, "changed_files", lambda base, head: ["dokimi/dokimi/novelty/x.py"])
    monkeypatch.setattr(cdrt, "load_pages", lambda: PAGES)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cdrt.main(["--base", "b", "--head", "h"]) == 0
    assert f"::warning file={PAGE}," in capsys.readouterr().out
    assert f"`{PAGE}`" in summary.read_text(encoding="utf-8")


def _tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=cdrt.REPO,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout:
        pytest.skip("not a git checkout: tracked files unknown")
    return [p for p in result.stdout.split("\0") if p]


def test_every_depends_on_glob_matches_a_tracked_file():
    """A glob that matches nothing is a stale mapping: the code moved and the page lost its trigger."""
    pages = cdrt.load_pages()
    assert pages, "no docs page declares depends_on"
    assert cdrt.stale_globs(pages, _tracked_files()) == []
