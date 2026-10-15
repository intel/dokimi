# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_changelog_fragment.py — the changelog-fragment gate."""

from __future__ import annotations

import importlib.util
import subprocess
import tomllib
import types
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT / "pyproject.toml"
_SCRIPT = PROJECT / "scripts" / "check_changelog_fragment.py"
_spec = importlib.util.spec_from_file_location("check_changelog_fragment", _SCRIPT)
assert _spec and _spec.loader
ccf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccf)

TEMPLATE = PROJECT / "changelog.d" / "fragment_template.md.j2"
CODE = ("M", "dokimi/dokimi/cli.py")


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("path, needed", [
    ("dokimi/dokimi/cli.py", True),
    ("dokimi/dokimi/products.yaml", True),        # shipped data is user-visible too
    ("dokimi/tests/test_cli.py", False),
    ("dokimi/docs/quickstart.md", False),
    ("dokimi/scripts/ci_local.py", False),
    (".github/workflows/ci.yml", False),
])
def test_which_paths_need_a_fragment(path, needed):
    assert ccf.needs_fragment([("M", path)]) is needed


def test_deleting_a_package_file_needs_a_fragment():
    assert ccf.needs_fragment([("D", "dokimi/dokimi/old.py")])


def test_a_written_fragment_passes(tmp_path):
    rel = "dokimi/changelog.d/20261014_x.md"
    _write(tmp_path, rel, "### Fixed\n\n- **Thing** — now works.\n")
    assert ccf.has_fragment([CODE, ("A", rel)], root=tmp_path)


@pytest.mark.parametrize("rel, status", [
    ("dokimi/changelog.d/20261014_x.md", "D"),     # deleting a fragment adds nothing
    ("dokimi/changelog.d/README.md", "M"),
    ("dokimi/changelog.d/sub/20261014_x.md", "A"),  # scriv does not recurse
    ("dokimi/changelog.d/20261014_x.txt", "A"),
    ("dokimi/CHANGELOG.md", "M"),
])
def test_files_that_are_not_fragments(tmp_path, rel, status):
    _write(tmp_path, rel, "### Fixed\n\n- **Thing** — now works.\n")
    assert not ccf.has_fragment([CODE, (status, rel)], root=tmp_path)


def test_the_unedited_template_has_no_content():
    # Render it the way `scriv create` does, with the categories from pyproject.
    jinja2 = pytest.importorskip("jinja2")
    scriv = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["scriv"]
    config = types.SimpleNamespace(categories=scriv["categories"])
    text = jinja2.Template(TEMPLATE.read_text(encoding="utf-8")).render(config=config)
    assert "### Added" in text
    assert not ccf.has_content(text)


def test_a_bare_category_heading_has_no_content():
    assert not ccf.has_content("### Fixed\n\n<!-- - **x** — y. -->\n")


def test_main_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(ccf, "changed_files", lambda base, head: [CODE])
    assert ccf.main(["--base", "b", "--head", "h"]) == 1
    assert "skip-changelog" in capsys.readouterr().err
    assert ccf.main(["--skip-label"]) == 0
    monkeypatch.setattr(ccf, "changed_files", lambda base, head: [("M", "dokimi/docs/a.md")])
    assert ccf.main([]) == 0


# changed_files() runs git; the tests above stub it, so these use a real repository.


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "dokimi/dokimi/a.py", "x = 1\n")
    _write(tmp_path, "other.txt", "o\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "pr")
    monkeypatch.setattr(ccf, "REPO", tmp_path)
    real = ccf.has_fragment
    monkeypatch.setattr(ccf, "has_fragment", lambda changed: real(changed, root=tmp_path))
    return tmp_path


def test_a_file_moved_out_of_the_package_counts_as_a_package_change(repo):
    _git(repo, "mv", "dokimi/dokimi/a.py", "other_a.py")
    _git(repo, "commit", "-qm", "move")
    changed = ccf.changed_files("main", "pr")
    assert ("D", "dokimi/dokimi/a.py") in changed
    assert ccf.needs_fragment(changed)


def test_a_change_that_landed_on_main_after_the_fork_is_not_the_prs(repo):
    _write(repo, "other.txt", "pr\n")
    _git(repo, "commit", "-qam", "pr")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "dokimi/dokimi/a.py", "x = 2\n")
    _git(repo, "commit", "-qam", "main moves on")
    changed = ccf.changed_files("main", "pr")
    assert changed == [("M", "other.txt")]
    assert not ccf.needs_fragment(changed)


def test_main_exit_code_against_a_real_repository(repo):
    _write(repo, "dokimi/dokimi/a.py", "x = 3\n")
    _git(repo, "commit", "-qam", "code")
    assert ccf.main(["--base", "main", "--head", "pr"]) == 1
    _write(repo, "dokimi/changelog.d/f.md", "### Fixed\n\n- **x** - y.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fragment")
    assert ccf.main(["--base", "main", "--head", "pr"]) == 0
