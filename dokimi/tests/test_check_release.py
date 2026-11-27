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


def _section(body: str) -> str:
    return f"# Changelog\n\n## [0.2.0] — 2026-11-01\n\n{body}\n\n## [0.1.0] — 2026-10-15\n\n- **First** — x.\n"


@pytest.mark.parametrize("body", [
    "### Fixed\n\n<!--\n- **Draft entry**\n-->",
    "### Fixed\n\n<!--\n- **Draft entry**",               # comment never closed
    "### Fixed\n\n<!-- a -->\n<!-- b\n- x\n-->",
    "### Fixed\n\n```markdown\n- **Example**\n```",         # a bullet in an example is no entry
    "### Fixed\n\n~~~\n- **Example**\n~~~",
])
def test_commented_or_fenced_bullets_are_not_entries(tmp_path, body):
    """#1290: a bullet inside a comment or a fence is not an entry."""
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=_section(body)))
    assert any("no entries under '## [0.2.0]'" in p for p in found)


def test_a_bullet_after_a_comment_or_fence_is_an_entry(tmp_path):
    body = "<!--\n- x\n-->\n```\n- y\n```\n- **Real** — z."
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=_section(body)))
    assert found == []


def test_a_heading_inside_a_fence_does_not_end_the_section():
    """#1290: --notes copies the whole section, fenced example included."""
    body = ("- **Example** — copy.\n\n```markdown\n## Example heading\n<a id='x'></a>\n```\n\n"
            "- **Follow-up** — mine.")
    assert cr.release_notes(_section(body), "0.2.0") == body
    longer = "````\n```\n## [0.1.0]\n````\n- tail"   # a shorter fence does not close a longer one
    assert cr.release_notes(_section(longer), "0.2.0") == longer
    tilde = "~~~\n## h\n~~~\n- tail"
    assert cr.release_notes(_section(tilde), "0.2.0") == tilde


def test_a_version_heading_inside_a_fence_is_not_a_section():
    """#1290: a fenced example of a version heading is not a release section."""
    text = "# Changelog\n\n```\n## [0.3.0]\n- x\n```\n"
    assert cr.release_notes(text, "0.3.0") is None
    assert cr.release_notes("", "0.3.0") is None


def test_a_version_heading_inside_a_comment_is_not_a_section(tmp_path):
    """#1290: a commented-out section is not a release section."""
    text = "# Changelog\n\n<!--\n## [0.2.0]\n- Hidden entry\n-->\n"
    assert cr.release_notes(text, "0.2.0") is None
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert any("no entries under '## [0.2.0]'" in p for p in found)


def test_a_fence_marker_inside_a_comment_opens_no_fence():
    """#1290: backticks in a comment do not hide the heading in the real fence."""
    body = "- Real entry\n\n<!--\n```\n-->\n\n```\n## Example heading\n```\n\n- Later entry"
    assert cr.release_notes(_section(body), "0.2.0") == body
    inline = "<!-- a --> ```\n- x\n<!-- b -->\n- y"   # text after a comment on a fence-like line
    assert cr.release_notes(_section(inline), "0.2.0") == inline


def test_inline_code_does_not_open_a_comment():
    """#1290: a `<!--` in an inline code span does not hide the next section boundary."""
    text = "## [0.2.0]\n\n- Document `<!--` syntax.\n\n## [0.1.0]\n\n- Old entry.\n"
    assert cr.release_notes(text, "0.2.0") == "- Document `<!--` syntax."
    assert cr._has_entry("- `<!--`")


def test_a_code_span_with_backticks_does_not_open_a_comment():
    """#1290: a longer delimiter lets a span hold backticks and a `<!--`."""
    text = "## [0.2.0]\n\n- Write ``a ` <!-- b`` here.\n\n## [0.1.0]\n\n- Old entry.\n"
    assert cr.release_notes(text, "0.2.0") == "- Write ``a ` <!-- b`` here."
    assert not cr._has_entry("A lone ` then <!-- open\n- hidden\n-->")   # no closer: literal tick


def test_a_multiline_code_span_does_not_open_a_comment():
    """#1290: a span may run over lines; a `<!--` inside it opens nothing."""
    text = ("## [0.2.0]\n\n- Document `first\nsecond <!-- third` marker.\n\n"
            "## [0.1.0]\n\n- Old entry.\n")
    assert cr.release_notes(text, "0.2.0") == "- Document `first\nsecond <!-- third` marker."


def test_backticks_in_a_comment_do_not_hide_its_end():
    """#1290: backticks inside a comment are literal, so they cannot shield `-->`."""
    assert cr._has_entry("<!-- ` --> `\n\n- Real entry.")


def test_an_escaped_comment_opener_opens_nothing():
    """#1290: ``\\<!--`` renders as text, so the bullet after it is visible."""
    assert cr._has_entry("Write \\<!-- literally.\n\n- Real entry.")


def test_a_comment_opened_on_the_heading_hides_the_section_body(tmp_path):
    """#1290: the release check keeps the comment state of the heading line."""
    text = "## [0.2.0] <!-- draft\n- Hidden entry\n-->\n\n## [0.1.0]\n- Old entry\n"
    assert not cr._section_has_entry(text, "0.2.0")
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert any("no entries under '## [0.2.0]'" in p for p in found)


def test_a_code_span_does_not_cross_a_heading_or_comment_block(tmp_path):
    """#1290: a tick on the heading cannot pair with one inside the next comment."""
    text = "## [0.2.0] `\n<!-- `\n- Hidden entry\n-->\n"
    assert not cr._section_has_entry(text, "0.2.0")
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert any("no entries under '## [0.2.0]'" in p for p in found)
    assert cr._has_entry("A `tick\n## Heading `\n- Real entry")


def test_a_code_span_does_not_cross_a_list_item(tmp_path):
    """#1290: a list item interrupts a paragraph, so two lone ticks pair with nothing."""
    text = "## [0.2.0]\n\nExample `literal\n- Real entry with ` marker\n"
    assert cr._section_has_entry(text, "0.2.0")
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert found == []


def test_an_indented_code_example_is_not_a_fence(tmp_path):
    """#1290: four spaces make code, not a fence, and notes keep the first line's indent."""
    text = "## [0.2.0]\n\n    ```\n\n- Real entry.\n"
    assert cr.release_notes(text, "0.2.0") == "    ```\n\n- Real entry."
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert found == []


def test_a_triple_backtick_span_is_not_a_fence(tmp_path):
    """#1290: ```example``` closes on its line, so it opens no fence."""
    text = "## [0.2.0]\n\n```example```\n\n- Real entry.\n"
    found, _ = cr.problems("v0.2.0", _project(tmp_path, changelog=text))
    assert found == []
    assert cr.release_notes("## [0.2.0]\n```js x\n## h\n```\n- y\n", "0.2.0") == "```js x\n## h\n```\n- y"


def test_an_unclosed_fence_runs_to_the_end():
    """#1290: an unclosed fence hides the rest of the file, as Markdown renders it."""
    text = "## [0.3.0]\n\n- a\n```\n## [0.2.0]\n- b\n"
    assert cr.release_notes(text, "0.3.0") == "- a\n```\n## [0.2.0]\n- b"


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
