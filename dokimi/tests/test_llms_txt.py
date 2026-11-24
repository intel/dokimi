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


def test_unicode_line_separator_does_not_move_evidence_to_another_physical_line():
    text = "# A\n> Summary\u2028on the same line\n## When to use\n## When not to use\n- [A](a)\n"
    assert [(finding.check, finding.line) for finding in llms_txt.check(text)] == [("links", 5)]


def test_reference_links_resolve_before_their_descriptions_are_checked():
    text = GOOD.replace('[Quickstart](https://acme.test/quickstart.md)', '[Quickstart][user    guide]')
    target = 'https://acme.test/v1_(guide).md'
    definition = f'\n[User Guide]: <{target}> "Guide"\n[user guide]: ignored.md\n'
    assert llms_txt.check(text + definition) == []
    assert llms_txt.check((text + definition).replace("\n", "\r\n")) == []
    missing = text.replace(': first charge in five minutes', '') + definition
    assert [(f.line, f.identity) for f in llms_txt.check(missing)] == [(15, f'Quickstart|{target}')]
    for label in ('[User Guide][]', '[User Guide]'):
        assert llms_txt.check(text.replace('[Quickstart][user    guide]', label) + definition) == []


def test_definitions_images_and_unresolved_references_are_not_documentation_links():
    text = GOOD.replace('[Quickstart](https://acme.test/quickstart.md)', '![icon][guide] and [Missing][unknown]')
    findings = llms_txt.check(text + '\n[guide]: https://example.com\n')
    assert [(f.check, f.severity, f.line) for f in findings] == [('links', 'fail', 0)]
    inline = GOOD.replace('- [Quickstart]', '- [note][Quickstart]')
    assert llms_txt.check(inline) == []


def test_link_like_text_in_a_title_does_not_create_another_link():
    text = GOOD.replace('https://acme.test/quickstart.md', 'guide "[Example](example)"')
    assert llms_txt.check(text) == []
    more = text.rstrip() + '; [Other](other.md)\n'
    assert [f.identity for f in llms_txt.check(more)] == ['Other|other.md']


def test_angle_brackets_delimit_the_destination_including_unbalanced_parentheses():
    text = GOOD.replace('https://acme.test/quickstart.md', '<https://example.test/function(a>')
    assert llms_txt.check(text) == []
    missing = text.replace(': first charge in five minutes', '')
    assert [f.identity for f in llms_txt.check(missing)] == ['Quickstart|https://example.test/function(a']


def test_inline_code_with_a_comment_opener_leaves_the_rest_of_the_index_visible():
    text = GOOD.replace('Payments for SaaS products.', 'Use `<!--` for a comment.')
    assert llms_txt.check(text) == []
    assert llms_txt.check(text.replace('`<!--`', '``a ` and <!--``')) == []
