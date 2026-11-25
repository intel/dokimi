# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from dokimi import llms_txt

GOOD = """# Acme API

> Payments for SaaS products.

## When to use

- Subscription billing

## When not to use

- Point of sale: use a terminal vendor

## Docs

- [Quickstart](https://acme.test/quickstart.md): first charge in five minutes
"""


def _checks(text: str) -> set[str]:
    return {f.check for f in llms_txt.check(text)}


def test_well_formed_file_passes():
    assert llms_txt.check(GOOD) == []


def test_missing_title_and_links_fail():
    found = llms_txt.check("just prose\n")
    assert {(f.check, f.severity) for f in found} >= {("title", "fail"), ("links", "fail")}


def test_missing_summary_is_reported_on_the_line_after_the_title():
    text = GOOD.replace("> Payments for SaaS products.\n", "")
    summary = [f for f in llms_txt.check(text) if f.check == "summary"]
    assert [(f.severity, f.line) for f in summary] == [("fail", 2)]


def test_when_not_heading_does_not_satisfy_when_to_use():
    text = GOOD.replace("## When to use\n", "## Overview\n")
    assert _checks(text) == {"when_to_use"}


def test_missing_negative_positioning_is_a_warning():
    text = GOOD.replace("## When not to use", "## Limits")
    assert _checks(text) == {"when_not_to_use"}


def test_undescribed_list_link_is_flagged_with_its_line():
    text = GOOD.replace(": first charge in five minutes", "")
    links = [f for f in llms_txt.check(text) if f.check == "links"]
    assert [f.line for f in links] == [text.splitlines().index(
        "- [Quickstart](https://acme.test/quickstart.md)") + 1]


def test_size_over_budget_is_flagged():
    assert "size" in _checks(GOOD + "x" * 9000) - _checks(GOOD)
    assert "size" not in {f.check for f in llms_txt.check(GOOD + "x" * 9000, token_budget=10_000)}


def test_fenced_examples_and_images_do_not_supply_document_structure():
    assert any(f.check == "title" for f in llms_txt.check("```markdown\n" + GOOD + "\n```"))
    assert any(f.check == "links" for f in llms_txt.check("# A\n> Summary\n- ![Logo](url)"))


def test_indented_heading_and_titled_list_links_are_checked():
    text = ' # A\n > Summary\n ## When to use\n ## When not to use\n1. [A](a "Title"): overview; [B](b)'
    assert [(f.check, f.line) for f in llms_txt.check(text)] == [("links", 5)]


def test_described_link_can_include_balanced_parentheses():
    text = GOOD.replace("https://acme.test/quickstart.md", "https://example.test/api/function(a(b))")
    assert text != GOOD
    assert llms_txt.check(text) == []


def test_inline_backticks_do_not_open_a_fence():
    text = GOOD.replace("## Docs", "```example```\n\n## Docs")
    assert text != GOOD
    assert llms_txt.check(text) == []


def test_html_comments_do_not_satisfy_structure_checks():
    assert {"title", "links"} <= _checks(f"<!--\n{GOOD}\n-->")


def test_inline_code_comment_delimiter_keeps_following_links():
    text = GOOD.replace('## Docs', '## Docs\n\nWrite `<!--` to begin a comment.')
    assert text != GOOD
    assert llms_txt.check(text) == []


def test_a_commented_out_link_after_an_escaped_backtick_fails_the_links_check():
    text = GOOD.replace('- [Quickstart](https://acme.test/quickstart.md)', r'\`<!-- [Hidden](url) -->\`')
    assert [(f.check, f.severity, f.message) for f in llms_txt.check(text)] == [
        ("links", "fail", "no Markdown links to the documentation")
    ]


def test_inline_examples_are_not_links_and_code_descriptions_are_visible():
    text = GOOD.replace('- [Quickstart](https://acme.test/quickstart.md)', 'Write links as `[Example](url)`')
    assert [(f.check, f.severity, f.message) for f in llms_txt.check(text)] == [
        ("links", "fail", "no Markdown links to the documentation")
    ]
    assert not _checks(GOOD.replace('first charge in five minutes', '`quickstart`'))


def test_code_span_across_a_soft_break_keeps_a_comment_literal():
    text = GOOD.replace('## Docs', '## Docs\n\nWrite `example\n<!--` to begin a comment.')
    assert not _checks(text)
