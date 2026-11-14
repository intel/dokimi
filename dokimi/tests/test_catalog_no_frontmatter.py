# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``not-a-skill-no-frontmatter``: a SKILL.md that is plain Markdown.

In the GitSkills corpus 165K of 1.88M distinct SKILL.md files have no frontmatter, 141K of them
starting with a ``#`` heading. They are not broken skills, so the finding says how
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
                                    "# T\n---  \nname: a\n---\n",
                                    "# Deck\n\n---\n# Generated metadata\nname: deck\ndescription: Use when decks.\n---\n",
                                    "# Deck\n---\ntitle: x\nname: deck\ndescription: Use when x.\n---\n",
                                    "# T\n---\n\nname: a\n---\n"])
def test_indented_or_late_frontmatter_is_a_broken_skill_not_plain_markdown(tmp_path, opener):
    # The loader's error stands for it, as for frontmatter after a blank line: no hint, and the scan fails.
    _plain(tmp_path, "broken", opener)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert "not-a-skill-no-frontmatter" not in codes
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1


@pytest.mark.parametrize("text", ["---\n\n# Deck Builder\n\nBuilds decks from text.\n",
                                   "---\n\n# Deck Builder\n\nBuilds decks from text.\n---\n",
                                   "---\n\n# Deck Builder\n\nBuilds decks from text.\n\nUsage: run deck.\n"])
def test_a_rule_then_prose_is_plain_markdown(tmp_path, text):
    # A first line of --- with no name: or description: after it is a rule, not a broken frontmatter.
    _plain(tmp_path, "deck", text)
    [review] = review_skills(read_skill_manifest(tmp_path))
    [finding] = review.findings
    assert (finding.level, finding.code) == ("error", "not-a-skill-no-frontmatter")


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: Deck Builder\ntags: [cards]\n---\n# Deck Builder\n\nBuilds decks.\n",  # YAML without name or description
        "---\ntitle: Deck: Builder\n---\n# Deck Builder\n\nBuilds decks.\n",  # broken YAML, no name or description
        "---\n  name: deck\n  description: Use when decks.\n---\n# Deck\n",  # indented keys
        "---\n- name: deck\n---\n# Deck\n",  # a list, not a mapping
        "---\nname: deck\ndescription: Use when decks.\n\n# Deck\n",  # no closing ---
        "---\n---\nname: deck\ndescription: Use when decks.\n---\n# Deck\n",  # an empty block first
        "---\n\n---\n# Deck\n\nBuilds decks.\n",  # an empty block with a blank line in it
        "---\n   \n---\n# Deck\n\nBuilds decks.\n",  # an empty block with a whitespace line in it
        "---\ntitle: x\ntags:\n  - a\n---\n# Deck\n",  # a list item among the keys: the block is still frontmatter
        "---\n\nname: deck\ndescription: Use when decks.\n# Deck\n",  # no closing ---, a blank first line
        "---\n[unclosed\n# Deck\n\nBuilds decks.\n",  # no closing ---, a flow collection opens it
    ],
)
def test_a_frontmatter_block_without_name_or_description_is_a_broken_skill_not_plain_markdown(tmp_path, text):
    # The block is frontmatter, so the loader's error stands and no plain-Markdown hint is given.
    _plain(tmp_path, "deck", text)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert "not-a-skill-no-frontmatter" not in codes


@pytest.mark.parametrize("text", ["---\n123: [unclosed\n---\n# Body\n",
                                   "---\n[unclosed\n---\n# Deck\n\nBuilds decks.\n",
                                   '---\n"name": "deck"\n"description": [Use when decks\n---\n# Deck\n',
                                   "---\n{name: deck, description: [Use when\n---\n# Deck\n"])
def test_broken_yaml_with_quoted_or_flow_keys_keeps_frontmatter_parse(tmp_path, text):
    _plain(tmp_path, "deck", text)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert codes == ["frontmatter-parse"]


def test_an_h1_inside_an_html_comment_is_not_the_title():
    lines = ["<!--", "# TODO: remove", "-->", "", "# Deck Builder", "", "Builds decks."]
    assert _block(no_frontmatter_message(lines, "deck", at_root=True)) == {
        "name": "deck-builder", "description": "Builds decks."}


def test_a_resumed_finding_without_a_code_is_not_plain_markdown():
    from dokimi.commands.catalog_scan import is_plain_markdown  # a row resumed from --jsonl may lack the key

    assert is_plain_markdown({"findings": [{"level": "warn", "message": "example"}]}) is False


def test_a_setext_h1_is_the_title():
    lines = ["Deck Builder", "===", "", "Builds decks."]
    assert _block(no_frontmatter_message(lines, "deck", at_root=True)) == {
        "name": "deck-builder", "description": "Builds decks."}


def test_an_indented_code_block_after_the_h1_is_not_the_description():
    desc = _block(no_frontmatter_message(["# Tool", "", "    npm install foo"], "tool", at_root=True))["description"]
    assert desc == "What tool does. Use when ..."  # the default: no paragraph, the code block is not prose


def test_a_name_from_the_h1_is_at_most_64_characters():
    assert _block(no_frontmatter_message(["# " + "a" * 70, "", "Builds."], "deck", at_root=True))["name"] == "a" * 64


def test_a_name_from_the_directory_is_at_most_64_characters():
    # Seventy characters is no spec name, so below the root the name is the H1's, or the directory's when there is none.
    assert _block(no_frontmatter_message(["Builds."], "d" * 70, at_root=False))["name"] == "d" * 64


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


@pytest.mark.parametrize("rule_index, plain", [(19, False), (20, True)])
def test_the_late_frontmatter_window_is_the_first_twenty_lines(tmp_path, rule_index, plain):
    # A --- on line 20 with name: after it is late frontmatter (a broken skill); on line 21 it is prose.
    text = "# Deck\n\n" + "Notes.\n" * (rule_index - 2) + "---\nname: deck\ndescription: Use when decks.\n---\n"
    _plain(tmp_path, "deck", text)
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert ("not-a-skill-no-frontmatter" in codes) is plain


def test_prose_after_a_comment_on_its_line_is_the_description():
    lines = ["# T", "", "<!-- note --> Real description."]
    assert _block(no_frontmatter_message(lines, "t", at_root=True))["description"] == "Real description."


def test_a_comment_that_opens_mid_line_is_taken_out_with_its_lines():
    lines = ["# T", "", "Intro words <!-- start", "hidden note", "-->", "after."]
    assert _block(no_frontmatter_message(lines, "t", at_root=True))["description"] == "Intro words after."


def test_a_comment_marker_in_a_code_span_or_unclosed_mid_line_is_text():
    # Only a closed comment, or one that opens a line and runs to the end, is taken out.
    assert _block(no_frontmatter_message(["# T", "", "Write `<!--` to open a comment.", "", "More text."], "t", at_root=True))[
        "description"] == "Write `<!--` to open a comment."
    assert _block(no_frontmatter_message(["# T", "", "Intro <!-- never closed.", "", "Second."], "t", at_root=True))[
        "description"] == "Intro <!-- never closed."
    assert _block(no_frontmatter_message(["Intro with `<!--` token.", "", "# Deck Builder", "", "Builds decks."], "d", at_root=True))[
        "name"] == "deck-builder"


def test_a_marker_or_backtick_does_not_reach_across_a_blank_line():
    # A mid-line comment or a code span stays in its paragraph; a comment that opens a line may span blank lines.
    stray = ["# T", "", "Intro <!-- never closed.", "", "Second.", "", "<!-- real -->"]
    assert _block(no_frontmatter_message(stray, "t", at_root=True))["description"] == "Intro <!-- never closed."
    paired = ["Intro with ` tick.", "", "# T", "", "<!--", "TODO: fill in", "-->", "", "Real.", "", "More ` here."]
    assert _block(no_frontmatter_message(paired, "t", at_root=True))["description"] == "Real."


def test_the_comment_and_code_span_branches_are_pinned():
    def description(lines):
        return _block(no_frontmatter_message(lines, "t", at_root=True))["description"]

    # An unclosed comment at a line start runs to the end of the file, over the heading and the text below it.
    assert description(["# T", "", "<!-- todo", "# Real Title", "", "Text."]) == "What t does. Use when ..."
    # A closed comment at a line start may hold a blank line.
    assert description(["# T", "", "<!--", "TODO", "", "more", "-->", "Real."]) == "Real."
    # A one-line comment on its own line closes on its line.
    assert description(["# T", "", "<!-- note -->", "", "Real."]) == "Real."
    # A code span holding a marker keeps it, and the later closing marker is not paired with it.
    assert description(["# T", "", "Write `<!--` then `-->` for a comment."]) == "Write `<!--` then `-->` for a comment."
    # A code span holds 1024 characters; one more and the comment inside it is taken out.
    kept = description(["# T", "", "`x <!-- c --> " + "a" * 1011 + "`"])
    cut = description(["# T", "", "`x <!-- c --> " + "a" * 1012 + "`"])
    assert kept.startswith("`x <!-- c --> ") and cut.startswith("`x  ")


def test_a_long_run_of_backticks_is_searched_in_linear_time():
    # A run of backticks that is not a fence, in one paragraph: the search must not backtrack over it.
    import time

    start = time.monotonic()
    no_frontmatter_message(["# T", "", "x " + "`" * 10000 + " end"], "t", at_root=True)
    assert time.monotonic() - start < 1.0


@pytest.mark.parametrize("first", ["[Docs](https://example.com)", "[Docs][docs]", "[docs]: https://example.com"])
def test_a_markdown_link_after_a_rule_is_prose(tmp_path, first):
    _plain(tmp_path, "deck", f"---\n{first}\n---\n# Deck\n\nBuilds decks.\n\n[docs]: https://example.com\n")
    codes = [f.code for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
    assert codes == ["not-a-skill-no-frontmatter"]
