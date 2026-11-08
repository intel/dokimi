# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Link targets, reference definitions, fenced blocks and directory names in ``dokimi.external.validate``.

Split from ``test_catalog_validate.py``, which is over the file-length cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.external import read_skill_manifest, review_skills


def _skill(root: Path, rel: str, name: str | None = None, description: str = "Use for things.",
           extra: str = "", body: str = "# Body\n") -> Path:
    """Write ``<root>/<rel>/SKILL.md``; ``name`` None leaves the key out of the frontmatter."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    head = f"name: {name}\n" if name is not None else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\n{head}description: {description}\n{extra}---\n{body}", encoding="utf-8")
    return skill_dir


def _reviews_by_name(root: Path) -> dict:
    entries = read_skill_manifest(root)
    return {e.name: r for e, r in zip(entries, review_skills(entries), strict=True)}


def _codes(review) -> list[str]:
    return [f.code for f in review.findings]


def test_a_link_destination_with_parentheses_to_an_existing_file_is_not_missing(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "parens", name="parens", description="Use for parens.",
                       body="See [the guide](references/guide(v2).md) and [a](references/a(b)(c).md).\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide(v2).md").write_text("# Guide\n", encoding="utf-8")
    (skill_dir / "references" / "a(b)(c).md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_link_destination_with_parentheses_to_a_missing_file_names_the_whole_path(tmp_path):
    _skill(tmp_path / "skills", "parens-missing", name="parens-missing", description="Use for parens.",
           body="See [the guide](references/guide(v2).md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'references/guide(v2).md'" in review.findings[0].message


def test_a_link_destination_nested_deeper_than_one_level_is_checked(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "nested", name="nested", description="Use for nested parens.",
                       body="See [a](references/a(b(c)).md) and [b](gone(x(y)).md).\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "a(b(c)).md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'gone(x(y)).md'" in review.findings[0].message


def test_a_link_destination_nested_past_32_levels_is_not_read(tmp_path):
    def nested(depth: int) -> str:
        return "a(" * depth + "b" + ")" * depth + ".md"
    _skill(tmp_path / "skills", "deep", name="deep", description="Use for deep parens.",
           body=f"See [a]({nested(32)}) and [b]({nested(33)}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert repr(nested(32)) in review.findings[0].message


def test_a_body_of_unclosed_links_is_reviewed_in_linear_time(tmp_path):
    # Unbounded, each ``](`` scanned to the end of the paragraph: over a minute for this 60 KB body.
    _skill(tmp_path / "skills", "hostile", name="hostile", description="Use for hostile bodies.",
           body="[](" * 20000 + "\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    # The body is 60 KB on purpose, so only its size is reported: no link finding.
    assert [f.code for f in review.findings] == ["body-too-large"]


def test_a_link_destination_nested_deeper_than_one_level_outside_the_skill_escapes(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "a(b(c)).md").write_text("# A\n", encoding="utf-8")
    _skill(tmp_path / "skills", "nested-escape", name="nested-escape", description="Use for nested escapes.",
           body="See [a](../../shared/a(b(c)).md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill"]


def test_a_backslash_escaped_underscore_in_a_link_destination_names_the_unescaped_file(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "escaped", name="escaped", description="Use for escaped links.",
                       body="See [notes](references/guide\\_notes.md) and [n][x].\n\n[x]: references/guide\\_notes.md\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide_notes.md").write_text("# Notes\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_backslash_escaped_underscore_outside_the_skill_still_escapes(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "guide_notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "escaped-out", name="escaped-out", description="Use for escaped links.",
           body="See [notes](../../shared/guide\\_notes.md).\n\n[n]: ../../shared/guide\\_notes.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill"]


@pytest.mark.parametrize("exists", [True, False])
def test_angle_bracket_destination_with_spaces_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "my notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "spaced-link", name="spaced-link", description="Use for spaced link.",
           body="See [notes](<../../shared/my notes.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill"]


@pytest.mark.parametrize("exists", [True, False])
def test_reference_definition_with_destination_on_the_next_line_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "next-line", name="next-line", description="Use for next line.",
           body="See [notes][n].\n\n[n]:\n  ../../shared/notes.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill"]


def test_a_tilde_line_inside_a_backtick_fence_does_not_close_it(tmp_path):
    _skill(tmp_path / "skills", "fences", name="fences", description="Use for fences.",
           body="```markdown\n~~~\n[example](missing.md)\n```\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_closing_fence_shorter_than_the_opening_one_does_not_close_it(tmp_path):
    _skill(tmp_path / "skills", "fence-run", name="fence-run", description="Use for fence run.",
           body="````\n```\n[example](missing.md)\n````\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_fence_indented_four_spaces_does_not_open_a_block(tmp_path):
    _skill(tmp_path / "skills", "indented", name="indented", description="Use for indented.",
           body="    ```\n[a](gone.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


def test_a_fence_in_a_blockquote_is_a_code_block_for_its_links(tmp_path):
    _skill(tmp_path / "skills", "quoted-fence", name="quoted-fence", description="Use for quoted fences.",
           body="> ```markdown\n> [example](missing.md)\n> ```\n\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_fence_under_a_list_item_indented_past_three_spaces_is_a_code_block(tmp_path):
    _skill(tmp_path / "skills", "list-fence", name="list-fence", description="Use for list fences.",
           body="- step one\n  - nested\n\n        ```markdown\n        [example](missing.md)\n        ```\n\n"
                "[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_blockquote_fence_ends_with_its_quote_when_it_has_no_closing_marker(tmp_path):
    _skill(tmp_path / "skills", "quote-end", name="quote-end", description="Use for quote ends.",
           body="> ```\n> example\n\n[real](missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_list_item_fence_ends_with_its_item_when_it_has_no_closing_marker(tmp_path):
    _skill(tmp_path / "skills", "item-end", name="item-end", description="Use for item ends.",
           body="- item\n  ```\n  example\n\n[real](missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_top_level_fence_after_a_list_item_is_not_closed_by_an_indented_marker(tmp_path):
    # the fence is at column 0, outside the item: its four-space ``` is code, not a closing marker
    _skill(tmp_path / "skills", "outdent", name="outdent", description="Use for outdented fences.",
           body="- item\n\n```\n    ```\n[example](missing.md)\n```\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_top_level_fence_after_a_list_item_in_a_quote_is_not_read_as_in_the_item(tmp_path):
    # the quote ends before the fence: its two-space indent is top level, so the unindented link is outside it
    _skill(tmp_path / "skills", "quoted-item", name="quoted-item", description="Use for quoted items.",
           body="> - item\n>\n  ```\n[example](missing.md)\n  ```\n\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_tab_indented_fence_under_a_list_item_is_inside_the_item(tmp_path):
    # a tab advances to the next four-column stop: the fence is at column 4, inside the item at column 2
    _skill(tmp_path / "skills", "tab-fence", name="tab-fence", description="Use for tab fences.",
           body="- item\n\t```\n\t[example](missing.md)\n\t```\n\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_deeper_quote_marker_inside_a_quoted_fence_is_code_not_a_closing_marker(tmp_path):
    _skill(tmp_path / "skills", "deep-quote", name="deep-quote", description="Use for deep quotes.",
           body="> ```\n> > ```\n> [example](missing.md)\n> ```\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_skills_named_through_a_subdirectory_dot_dot_are_not_mirror_drift(tmp_path):
    # "skills/alpha/sub/.." is the skill directory "alpha": two such skills are not copies of each other.
    _skill(tmp_path / "skills", "alpha", name="alpha", description="Use for alpha work.", body="# Alpha\n")
    _skill(tmp_path / "skills", "beta", name="beta", description="Use for beta work.", body="# Beta\n")
    (tmp_path / "skills" / "alpha" / "sub").mkdir()
    (tmp_path / "skills" / "beta" / "sub").mkdir()
    (tmp_path / "skills.yaml").write_text(
        "skills:\n"
        "  - name: alpha\n    catalog_path: skills/alpha/sub/..\n"
        "  - name: beta\n    catalog_path: skills/beta/sub/..\n",
        encoding="utf-8")

    reviews = _reviews_by_name(tmp_path)
    assert _codes(reviews["alpha"]) == []
    assert _codes(reviews["beta"]) == []


@pytest.mark.parametrize("body", [
    '[a](gone.md "t")\n',
    "[a](gone.md 't')\n",
    "[a](gone.md (t))\n",
    "[a]( gone.md )\n",
    "```x``` then\n[a](gone.md)\n",
])
def test_link_forms_and_fence_lookalikes_are_still_checked(tmp_path, body):
    _skill(tmp_path / "skills", "forms", name="forms", description="Use for forms.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


def test_a_link_inside_parentheses_does_not_take_the_closing_one(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "wrapped", name="wrapped", description="Use for wrapped.",
                       body="(see [a](here.md))\n")
    (skill_dir / "here.md").write_text("# Here\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_reference_label_before_a_fence_does_not_take_a_destination_after_it(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "label-fence", name="label-fence", description="Use for label fence.",
           body="See [notes][n].\n\n[n]:\n```\ncode\n```\n../../shared/notes.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_link_with_a_query_to_an_existing_file_is_checked_by_its_path(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "query-link", name="query-link", description="Use for query links.",
                       body="See [the guide](references/guide.md?raw=1).\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_query_bearing_link_outside_the_skill_still_escapes(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    _skill(tmp_path / "skills", "query-escape", name="query-escape", description="Use for query escapes.",
           body="See [the guide](../../shared/guide.md?raw=1#top).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill"]


def test_a_fence_under_an_inner_list_item_ends_with_the_outer_item(tmp_path):
    # the fence is at column 2, inside the outer item (content column 2) but outside the inner one (column 4)
    _skill(tmp_path / "skills", "nested-item", name="nested-item", description="Use for nested items.",
           body="- outer\n  - inner\n\n  ```\n  example\n\n[real](missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_fence_opened_on_a_list_item_line_is_a_code_block(tmp_path):
    _skill(tmp_path / "skills", "item-opener", name="item-opener", description="Use for item openers.",
           body="- ```markdown\n  [example](missing.md)\n  ```\n\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


@pytest.mark.parametrize("exists", [True, False])
def test_a_multiline_inline_link_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "multiline", name="multiline", description="Use for multiline links.",
           body="See [notes](\n../../shared/notes.md\n).\n" if exists
           else "See [guide](\nreferences/missing.md\n).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == (["link-escapes-skill"] if exists else ["link-missing"])


@pytest.mark.parametrize("body", [
    "See [guide](\n\nreferences/missing.md)\n",
    "See [guide](<\nreferences/missing.md>)\n",
])
def test_a_link_split_by_a_blank_line_or_an_angle_bracket_line_end_is_not_a_link(tmp_path, body):
    _skill(tmp_path / "skills", "split-link", name="split-link", description="Use for split links.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_link_inside_a_code_span_is_not_checked(tmp_path):
    _skill(tmp_path / "skills", "code-span", name="code-span", description="Use for code spans.",
           body="Example: `[guide](missing.md)` and [real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


@pytest.mark.parametrize("exists", [True, False])
def test_a_reference_definition_in_a_blockquote_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "quoted-ref", name="quoted-ref", description="Use for quoted references.",
           body="> [notes][n]\n>\n> [n]: ../../shared/notes.md\n" if exists
           else "> [notes][n]\n>\n> [n]: references/missing.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == (["link-escapes-skill"] if exists else ["link-missing"])


def test_an_escaped_backtick_does_not_open_a_code_span(tmp_path):
    _skill(tmp_path / "skills", "escaped-tick", name="escaped-tick", description="Use for escaped ticks.",
           body="\\`[a](missing.md)\\`\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_percent_encoded_link_names_the_decoded_file(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "pct", name="pct", description="Use for percent links.",
                       body="[g](references/guide%20notes.md) [h](a%23b.md)\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide notes.md").write_text("# Notes\n", encoding="utf-8")
    (skill_dir / "a#b.md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_percent_encoded_link_to_a_missing_file_names_the_encoded_path(tmp_path):
    _skill(tmp_path / "skills", "pct-missing", name="pct-missing", description="Use for percent links.",
           body="[g](references/guide%20notes.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'references/guide%20notes.md'" in review.findings[0].message


def test_an_escaped_bracket_opens_no_link(tmp_path):
    _skill(tmp_path / "skills", "escaped-bracket", name="escaped-bracket", description="Use for escaped brackets.",
           body="\\[guide](missing.md)\n[guide\\](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


@pytest.mark.parametrize("body", ["See [guide](missing.md 'title').\n", "See [guide](missing.md ).\n"])
def test_a_link_with_a_single_quoted_title_or_a_space_before_the_paren_is_checked(tmp_path, body):
    _skill(tmp_path / "skills", "titled", name="titled", description="Use for titles.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_code_span_ending_in_a_backslash_closes_before_the_next_link(tmp_path):
    _skill(tmp_path / "skills", "trailing-slash", name="trailing-slash", description="Use for backslashes.",
           body="Example: `[example](missing.md)\\` and [real](gone.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'gone.md'" in review.findings[0].message


@pytest.mark.parametrize("body", ["Example: ``[guide](missing.md)``\n", "Example: `[guide](missing.md)\nsee above`\n"])
def test_a_multi_backtick_or_multiline_code_span_is_literal(tmp_path, body):
    _skill(tmp_path / "skills", "span-literal", name="span-literal", description="Use for code spans.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_fragment_inside_a_code_span_is_part_of_the_file_name(tmp_path):
    (tmp_path / "skills" / "shared").mkdir(parents=True)
    (tmp_path / "skills" / "shared" / "notes#draft.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "span-fragment", name="span-fragment", description="Use for span fragments.",
           body="Read `../shared/notes#draft.md`.\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["path-escapes-skill"]


def test_a_fence_closing_marker_indented_past_the_opening_one_does_not_close_it(tmp_path):
    _skill(tmp_path / "skills", "deep-close", name="deep-close", description="Use for deep fences.",
           body="- item\n  ```\n      ```\n  [example](missing.md)\n  ```\n\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_host_path_link_is_an_escape_whether_or_not_the_host_file_exists(tmp_path):
    # /etc/passwd exists on most hosts and the nonexistent name does not: both are classed by name alone.
    _skill(tmp_path / "skills", "host-path", name="host-path", description="Use for host paths.",
           body="See [a](../../../../../../../../etc/passwd) and [b](../../../../../../../../etc/nonexist).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-escapes-skill", "link-escapes-skill"]


@pytest.mark.parametrize("body", ["See [![CI](badge.svg)](missing.md).\n", "See [see [docs]](missing.md).\n"])
def test_a_link_whose_text_holds_brackets_is_still_checked(tmp_path, body):
    skill_dir = _skill(tmp_path / "skills", "nested-text", name="nested-text", description="Use for nested text.",
                       body=body)
    (skill_dir / "badge.svg").write_text("<svg/>", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_backtick_runs_of_unequal_length_do_not_make_a_code_span(tmp_path):
    _skill(tmp_path / "skills", "tick-runs", name="tick-runs", description="Use for backtick runs.",
           body="Text ``x [a](missing.md) `y\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'missing.md'" in review.findings[0].message


def test_a_literal_trailing_backslash_in_a_code_span_does_not_escape_its_closing_backtick(tmp_path):
    _skill(tmp_path / "skills", "literal-slash", name="literal-slash", description="Use for backslashes.",
           body="Example: `[example](missing.md)\\`\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


@pytest.mark.parametrize("body", ["Read `\n../shared/notes.md\n`.\n", "Read ``../shared/a`b.md``.\n"])
def test_a_multiline_or_backtick_named_code_span_path_is_checked_as_a_path(tmp_path, body):
    (tmp_path / "skills" / "shared").mkdir(parents=True)
    (tmp_path / "skills" / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (tmp_path / "skills" / "shared" / "a`b.md").write_text("# A\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach-span", name="reach-span", description="Use for code span paths.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["path-escapes-skill"]


def test_an_escaped_parenthesis_in_a_link_destination_is_one_target(tmp_path):
    # the destination is ``a\)b.md``: one file, read once; the splitter's one-level reader would cut it at the backslash
    skill_dir = _skill(tmp_path / "skills", "escaped-paren", name="escaped-paren",
                       description="Use for escaped parentheses.", body="See [x](a\\)b.md).\n")
    (skill_dir / "a)b.md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == []


@pytest.mark.parametrize("body, missing", [
    ("- run `foo\n- then see [x](missing.md)\n- and `bar` here\n", "missing.md"),
    ("- use ` for quotes\n- see [x](missing.md) and `foo`\n", "missing.md"),
    ("# h ` here\n[x](missing.md) `\n", "missing.md"),
])
def test_a_code_span_does_not_run_across_list_items_or_headings(tmp_path, body, missing):
    _skill(tmp_path / "skills", "span-blocks", name="span-blocks", description="Use for span blocks.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert f"'{missing}'" in review.findings[0].message


def test_a_code_span_naming_a_path_outside_the_checkout_is_never_read(tmp_path):
    # the span is only read for its text; a path that resolves outside the checkout is not statted or reported
    _skill(tmp_path / "skills", "span-escape", name="span-escape", description="Use for span escapes.",
           body="Read `../../../../../../../../etc/passwd`.\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


@pytest.mark.parametrize("body", [
    "> You might also find this helpful: [Title](URL) — one-sentence description.\n",
    "See [a](LINK) and [b](PATH).\n",
    "See [a](<URL>) and [b](< PATH >).\n",
    "See [a](URL#section) and [b](URL?x=1).\n",
    "See [a](...) and [b](<...>).\n",
    "[a]: URL\n",
    "See [a](url) [b](Todo) [c](tbd) [d](Uri) [e](HREF) [f](link).\n",
])
def test_a_placeholder_target_is_not_checked(tmp_path, body):
    _skill(tmp_path / "skills", "placeholder", name="placeholder", description="Use for placeholders.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


@pytest.mark.parametrize("target", [
    "LICENSE", "README", "CHANGELOG", "NOTICE", "URLS", "PATH-x", "some/URL", "URL.md", "URL-x.md",
])
def test_a_target_outside_the_placeholder_list_is_checked(tmp_path, target):
    _skill(tmp_path / "skills", "lookalike", name="lookalike", description="Use for lookalikes.",
           body=f"See [a]({target}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


def test_a_placeholder_next_to_a_real_missing_link_leaves_that_link_checked(tmp_path):
    _skill(tmp_path / "skills", "mixed", name="mixed", description="Use for mixed links.",
           body="Try [Title](URL) or [real](gone.md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'gone.md'" in review.findings[0].message


@pytest.mark.parametrize("body", [
    "Example: `[Title](URL)`\n",
    "```markdown\n> You might also find this helpful: [Title](URL) — one-sentence description.\n```\n",
    "> ```\n> [Title](URL)\n> ```\n",
])
def test_a_placeholder_link_in_a_code_span_or_fence_is_not_checked(tmp_path, body):
    _skill(tmp_path / "skills", "in-code", name="in-code", description="Use for code.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []
