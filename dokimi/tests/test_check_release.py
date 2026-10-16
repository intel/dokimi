# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_release.py — the release-readiness check."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release.py"
_spec = importlib.util.spec_from_file_location("check_release", _SCRIPT)
assert _spec and _spec.loader
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

CHANGELOG = """# Changelog

## [Unreleased]

<!-- scriv-insert-here -->

<a id='changelog-0.2.0'></a>
## [0.2.0] — 2026-11-01

### Added

- **New thing** — it does more.

<a id='changelog-0.1.0'></a>
## [0.1.0] — 2026-10-15

### Added

- **First** — release.
"""


def _project(tmp_path: Path, version="0.2.0", changelog=CHANGELOG, fragments=()) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "dokimi"\nversion = "{version}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("how to", encoding="utf-8")
    # Not fragments: scriv reads *.md only.
    (tmp_path / "changelog.d" / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "changelog.d" / "fragment_template.md.j2").write_text("t", encoding="utf-8")
    for name in fragments:
        (tmp_path / "changelog.d" / name).write_text("### Fixed\n\n- x\n", encoding="utf-8")
    return tmp_path


def test_release_notes_are_one_section():
    notes = cr.release_notes(CHANGELOG, "0.2.0")
    assert notes == "### Added\n\n- **New thing** — it does more."
    assert cr.release_notes(CHANGELOG, "0.1.0").endswith("- **First** — release.")
    assert cr.release_notes(CHANGELOG, "0.3.0") is None
    assert cr.release_notes(CHANGELOG, "0.1") is None    # no prefix match on 0.1.0
    assert cr.release_notes("## [0a2b0]\n\n- x\n", "0.2.0") is None  # the dots are literal


def test_a_heading_must_start_its_line():
    text = "## [Unreleased]\n\n- see ## [0.3.0] below\n\n### [0.3.0]\n\n- x\n"
    assert cr.release_notes(text, "0.3.0") is None


def test_a_ready_tree_passes(tmp_path):
    found, notes = cr.problems("v0.2.0", _project(tmp_path))
    assert found == []
    assert notes and "New thing" in notes


def test_tag_must_match_the_version(tmp_path):
    found, _ = cr.problems("v0.2.1", _project(tmp_path))
    assert any("does not match project.version '0.2.0'" in p for p in found)
    found, _ = cr.problems("0.2.0", _project(tmp_path / "b"))   # missing the v
    assert found
    found, _ = cr.problems("v0.2.0.1", _project(tmp_path / "c"))  # the tag only starts with it
    assert found


def test_a_section_with_only_a_category_heading_has_no_entries(tmp_path):
    changelog = "# Changelog\n\n## [0.2.0] — 2026-11-01\n\n### Added\n\n<!-- - x -->\n"
    found, notes = cr.problems("v0.2.0", _project(tmp_path, changelog=changelog))
    assert notes == "### Added\n\n<!-- - x -->"
    assert any("no entries under '## [0.2.0]'" in p for p in found)


def test_version_needs_a_changelog_section(tmp_path):
    found, _ = cr.problems("v0.3.0", _project(tmp_path, version="0.3.0"))
    assert any("scriv collect --version 0.3.0" in p for p in found)


def test_uncollected_fragments_block_a_release(tmp_path):
    found, _ = cr.problems("v0.2.0", _project(tmp_path, fragments=["20261101_x.md"]))
    assert any("20261101_x.md" in p for p in found)


def test_main_on_a_ready_tree_prints_the_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cr, "PROJECT", _project(tmp_path))
    assert cr.main(["v0.2.0"]) == 0
    assert capsys.readouterr().out == ""
    assert cr.main(["v0.2.0", "--notes"]) == 0
    out = capsys.readouterr()
    assert out.out == "### Added\n\n- **New thing** — it does more.\n"
    assert out.err == ""


@pytest.mark.parametrize("tag, fragments, message", [
    ("v0.2.1", (), "does not match project.version"),
    ("v0.2.0", ("20261101_x.md",), "uncollected fragments: 20261101_x.md"),
])
def test_main_fails_a_tree_that_is_not_ready(tmp_path, monkeypatch, capsys, tag, fragments, message):
    monkeypatch.setattr(cr, "PROJECT", _project(tmp_path, fragments=fragments))
    assert cr.main([tag, "--notes"]) == 1
    out = capsys.readouterr()
    assert out.out == ""   # no notes for a release that does not go ahead
    assert out.err.startswith("::error::")
    assert message in out.err


def test_this_repository_changelog_is_well_formed():
    text = (cr.PROJECT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    assert text.count("<!-- scriv-insert-here -->") == 1
