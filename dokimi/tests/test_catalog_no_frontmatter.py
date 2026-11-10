# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``not-a-skill-no-frontmatter``: a SKILL.md that is plain Markdown.

In the GitSkills corpus 165K of 1.88M distinct SKILL.md files have no frontmatter, 141K of them
starting with a ``#`` heading (scratch S4 #9). They are not broken skills, so the finding says how
to make one, and the scan summary counts them apart.
"""

from __future__ import annotations

import json

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.external import read_skill_manifest, review_skills
from dokimi.external.no_frontmatter import no_frontmatter_message
from dokimi.utils import InputError


def main(argv):
    """Run the CLI on ``argv``; the exit code (0 when nothing raised)."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        return exc.code
    return 0


def _plain(root, rel, text):
    skill_dir = root / "skills" / rel
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def _block(finding):
    message = finding if isinstance(finding, str) else finding.message
    return yaml.safe_load(message.split("\n", 1)[1].strip("-\n"))


def test_plain_markdown_gets_a_frontmatter_hint(tmp_path):
    _plain(tmp_path, "deck", "# Deck Builder\n\nBuilds slide decks from an outline.\nKeeps the brand.\n\n## Steps\n")
    [review] = review_skills(read_skill_manifest(tmp_path))
    [finding] = review.findings
    assert (finding.level, finding.code) == ("error", "not-a-skill-no-frontmatter")
    assert _block(finding) == {"name": "deck-builder",
                               "description": "Builds slide decks from an outline. Keeps the brand."}


@pytest.mark.parametrize(
    ("lines", "name", "description"),
    [
        ([], "dir-name", "What dir-name does. Use when ..."),
        (["# 部署助手", "", "部署服务。"], "dir-name", "部署服务。"),  # non-ASCII H1: the directory name
        (["﻿# Tool: run it!", "```", "not this", "```", "Real text."], "tool-run-it", "Real text."),
        (["Intro line with no heading."], "dir-name", "Intro line with no heading."),
        (["# 2048", "", "Play it."], "2048", "Play it."),  # quoted, or YAML reads a number
        (["# 2026-02-30", "", "Notes."], "2026-02-30", "Notes."),  # quoted, or YAML reads an invalid date
        (["````", "```", "# Example", "Example prose.", "````", "# Real Title", "Real text."],
         "real-title", "Real text."),  # a shorter fence run does not close a four-backtick one
        (["# Q", "> Master it."], "q", "Master it."),
        # The H1 is found before the paragraph is taken: an intro above it, a badge line, an H1 inside a paragraph.
        (["Introductory note.", "", "# Deck Builder", "", "Builds slide decks."], "deck-builder", "Builds slide decks."),
        (["[![CI](https://x/b.svg)](https://x)", "", "# Badge Tool", "", "Does badges."], "badge-tool", "Does badges."),
        (["Intro", "# T", "More"], "t", "More"),
        (["# Yes"], "yes", "What yes does. Use when ..."),
        (["# T", "", "| a | b |", "", "Text: with \"quotes\""], "t", "Text: with \"quotes\""),
    ],
)
def test_the_hint_block_is_valid_yaml(lines, name, description):
    finding = no_frontmatter_message(lines, "Dir_Name")
    assert _block(finding) == {"name": name, "description": description}


def test_a_directory_named_as_an_invalid_date_gives_a_valid_block():
    finding = no_frontmatter_message(["Intro line with no heading."], "2026-02-30")
    assert _block(finding) == {"name": "2026-02-30", "description": "Intro line with no heading."}


def test_a_long_first_paragraph_is_cut_at_a_word():
    finding = no_frontmatter_message(["# A", "word " * 200], "a")
    description = _block(finding)["description"]
    assert len(description) <= 304 and description.endswith(" ...")


def test_broken_yaml_is_still_frontmatter_parse(tmp_path):
    _plain(tmp_path, "bad", "---\nname: bad\ndescription: [unclosed\n---\n# Body\n")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert [f.code for f in review.findings] == ["frontmatter-parse"]


@pytest.mark.parametrize("per_skill", [[], ["--per-skill"]])
def test_the_summary_counts_plain_markdown_apart(tmp_path, capsys, per_skill):
    _plain(tmp_path, "plain", "# Plain\n\nJust notes.\n")
    _plain(tmp_path, "good", "---\nname: good\ndescription: Use when testing.\n---\n# Body\n")
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", *per_skill]) == 1
    out = capsys.readouterr().out
    assert "1 error, of them 1 plain Markdown with no frontmatter" in out


def test_ignoring_the_code_is_refused(tmp_path, capsys):
    _plain(tmp_path, "plain", "# Plain\n")
    with pytest.raises(InputError, match="not-a-skill-no-frontmatter"):
        main(["catalog", "check", "--root", str(tmp_path), "--ignore", "not-a-skill-no-frontmatter"])


def test_the_json_row_has_the_finding(tmp_path, capsys):
    _plain(tmp_path, "plain", "# Plain\n\nNotes.\n")
    main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--format", "json"])
    [row] = json.loads(capsys.readouterr().out)["skills"]
    assert row["state"] == "error"
    assert [f["code"] for f in row["findings"]] == ["not-a-skill-no-frontmatter"]


def test_frontmatter_after_a_blank_line_is_not_plain_markdown(tmp_path):
    skill = tmp_path / "skills" / "late"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("\n---\nname: late\ndescription: Use when late.\n---\n# Late\n", encoding="utf-8")
    from dokimi.external import read_skill_manifest, review_skills

    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert "not-a-skill-no-frontmatter" not in codes
