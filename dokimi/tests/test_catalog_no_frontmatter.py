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
    # Below the root the name is the directory's: the loader's name-mismatch check would reject the H1's name.
    assert _block(finding) == {"name": "deck",
                               "description": "Builds slide decks from an outline. Keeps the brand."}


def test_the_pasted_hint_gives_no_name_mismatch(tmp_path):
    _plain(tmp_path, "deck", "# Deck Builder\n\nBuilds decks.\n")
    [review] = review_skills(read_skill_manifest(tmp_path))
    [finding] = review.findings
    skill = tmp_path / "skills" / "deck" / "SKILL.md"
    skill.write_text(finding.message.split("\n", 1)[1] + "\n# Deck Builder\n\nBuilds decks.\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert "name-mismatch" not in [f.code for f in review.findings]


@pytest.mark.parametrize(
    ("at_root", "dir_name", "name"),
    [
        (False, "deck", "deck"),  # below the root: the directory name, which the loader checks
        (False, "Deck_Builder", "deck-builder"),  # not a valid spec name: the H1 is the best start
        (True, "deck", "deck-builder"),  # the root skill's name is not checked against its directory
    ],
)
def test_the_name_follows_the_loader_name_check(at_root, dir_name, name):
    finding = no_frontmatter_message(["# Deck Builder", "", "Builds decks."], dir_name, at_root=at_root)
    assert _block(finding)["name"] == name


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
        (["#\tTag Tool", "", "Does tags."], "tag-tool", "Does tags."),  # a tab after the # is an H1 too
        (["# T", "", "Tab\x7f and a\x9f C1 control."], "t", "Tab and a C1 control."),  # PyYAML rejects these raw
        (["# T", "", "<!-- a", "multi line comment", "-->", "", "Real."], "t", "Real."),  # a comment spans lines
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


def test_the_text_row_keeps_the_hint_block_in_its_row(tmp_path, capsys):
    _plain(tmp_path, "plain", "# Plain\n\nNotes.\n")
    main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    lines = capsys.readouterr().out.splitlines()
    # The block's lines are indented under the row, so a corpus scan reads row by row.
    assert not [line for line in lines if line.startswith(("---", "name:", "description:"))]
    assert "    ---" in lines


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


@pytest.mark.parametrize("per_skill", [[], ["--per-skill"]])
@pytest.mark.parametrize("example", ["```yaml\n---\nname: example\ndescription: Use when testing.\n---\n```\n",
                                     "> ---\n> name: example\n> description: Use when testing.\n> ---\n"])
def test_a_fenced_frontmatter_example_in_plain_markdown_is_still_plain_markdown(tmp_path, capsys, per_skill, example):
    # Docs about skills show a frontmatter block in a fence or a quote near the top: that is prose, not misplaced frontmatter.
    text = "# Tool\n\nExplains metadata.\n\n" + example
    _plain(tmp_path, "tool", text)
    [review] = review_skills(read_skill_manifest(tmp_path))
    [finding] = review.findings
    assert (finding.level, finding.code) == ("error", "not-a-skill-no-frontmatter")
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", *per_skill]) == 1
    assert "1 error, of them 1 plain Markdown with no frontmatter" in capsys.readouterr().out


@pytest.mark.parametrize("opener", ["  ---\nname: indent\ndescription: y\n  ---\n# X\n",
                                    "\n---\nname: late\ndescription: y\n---\n# Late\n",
                                    "<!-- generated -->\n---\nname: deck\ndescription: Use when decks.\n---\n# Deck\n",
                                    "# T\n\n\n\n\n---\nname: a\ndescription: b\n---\n",
                                    "# T\n---\ndescription: b\n---\n",
                                    "# T\n---  \nname: a\n---\n"])
def test_indented_or_late_frontmatter_is_a_broken_skill_not_plain_markdown(tmp_path, opener):
    # The loader's error stands for it, as for frontmatter after a blank line: no hint, and the scan fails.
    _plain(tmp_path, "broken", opener)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert "not-a-skill-no-frontmatter" not in codes
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1


def test_a_root_skill_gets_the_h1_name_not_the_directory_name(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "repo-dir"  # a valid spec name, so only at_root keeps it out of the hint
    root.symlink_to(real)
    (root / "SKILL.md").write_text("# Deck Builder\n\nBuilds decks.\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(root))
    assert _block(review.findings[0])["name"] == "deck-builder"


def test_the_count_leaves_out_other_load_errors(tmp_path, capsys):
    _plain(tmp_path, "plain", "# Plain\n\nNotes.\n")
    _plain(tmp_path, "bad", "---\nname: bad\ndescription: [unclosed\n---\n# Body\n")
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1
    assert "2 error, of them 1 plain Markdown" in capsys.readouterr().out


@pytest.mark.parametrize("text", ["", " \n\n", "﻿\n", "﻿---\nname: bom\ndescription: [unclosed\n---\n# B\n"])
def test_an_empty_file_or_a_bom_before_frontmatter_is_not_plain_markdown(tmp_path, text):
    _plain(tmp_path, "odd", text)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert "not-a-skill-no-frontmatter" not in codes
