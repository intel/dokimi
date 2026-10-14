# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_changelog_fragment.py — the changelog-fragment gate."""

from __future__ import annotations

import importlib.util
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


def test_main_against_git(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ccf, "changed_files", lambda base, head: [CODE])
    assert ccf.main(["--base", "b", "--head", "h"]) == 1
    assert "skip-changelog" in capsys.readouterr().err
    assert ccf.main(["--skip-label"]) == 0
    monkeypatch.setattr(ccf, "changed_files", lambda base, head: [("M", "dokimi/docs/a.md")])
    assert ccf.main([]) == 0
