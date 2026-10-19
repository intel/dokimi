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


@pytest.mark.parametrize("text", [
    "---\r\ndepends_on:\r\n  - a/**\r\n---\r\nx\r\n",
    "\ufeff---\ndepends_on:\n  - a/**\n---\nx\n",
    "---\ndepends_on:\n  - a/**\n---\n",  # no content after closing ---, no final newline
    "---\ndepends_on:\n  - a/**\n---",    # no content and no final newline after closing ---
])
def test_crlf_and_bom_pages_keep_their_depends_on(text):
    assert cdrt.depends_on(text) == ["a/**"]


def _git_repo(path: Path) -> None:
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"],
                ["config", "user.name", "t"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *cmd], cwd=path, check=True)


def _commit(path: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True)


def test_changed_files_reports_both_sides_of_a_rename(tmp_path, monkeypatch):
    _git_repo(tmp_path)
    (tmp_path / "old.py").write_text("print('x' * 100)\n" * 20, encoding="utf-8")
    _commit(tmp_path, "base")
    subprocess.run(["git", "tag", "base"], cwd=tmp_path, check=True)
    (tmp_path / "old.py").rename(tmp_path / "new.py")
    _commit(tmp_path, "move")
    monkeypatch.setattr(cdrt, "REPO", tmp_path)
    assert sorted(cdrt.changed_files("base", "HEAD")) == ["new.py", "old.py"]


def test_main_exits_2_for_a_base_git_cannot_diff(tmp_path, monkeypatch, capsys):
    _git_repo(tmp_path)
    (tmp_path / "f").write_text("x", encoding="utf-8")
    _commit(tmp_path, "base")
    monkeypatch.setattr(cdrt, "REPO", tmp_path)
    assert cdrt.main(["--base", "no-such-ref", "--head", "HEAD"]) == 2
    assert "no-such-ref" in capsys.readouterr().err


def test_load_pages_keys_are_repo_relative(tmp_path):
    docs = tmp_path / "dokimi" / "docs"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("---\ndepends_on: [x/**]\n---\n", encoding="utf-8")
    (docs / "b.md").write_text("# no front matter\n", encoding="utf-8")
    assert cdrt.load_pages(tmp_path) == {"dokimi/docs/a.md": ["x/**"]}


@pytest.mark.parametrize("front", ["depends_on: a/**", "depends_on: [a"])
def test_a_malformed_page_is_named_and_exits_1_not_a_traceback(tmp_path, monkeypatch, capsys, front):
    docs = tmp_path / "dokimi" / "docs"
    docs.mkdir(parents=True)
    (docs / "bad.md").write_text(f"---\n{front}\n---\n", encoding="utf-8")
    monkeypatch.setattr(cdrt, "changed_files", lambda base, head: [])
    monkeypatch.setattr(cdrt, "REPO", tmp_path)
    real = cdrt.load_pages
    monkeypatch.setattr(cdrt, "load_pages", lambda: real(tmp_path))
    assert cdrt.main([]) == 1
    assert "::error file=dokimi/docs/bad.md" in capsys.readouterr().out
