# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""trace prefilter: an opening YAML frontmatter block is metadata, not a claim (#728)."""

from dokimi.context.prefilter import claims_from_guidance


def test_frontmatter_is_not_a_claim():
    text = ("---\nname: ci-first\ndescription: run `make ci-local` before push\nmetadata:\n  type: feedback\n---\n\n"
            "Always run `make ci-local` before push.\n")
    claims = claims_from_guidance(text)
    assert [c.text for c in claims] == ["Always run `make ci-local` before push."]
    assert claims[0].id == "s0001"


def test_a_later_rule_line_is_not_frontmatter():
    claims = claims_from_guidance("Rule one `ls`.\n\n---\nname: not frontmatter\n---\n")
    assert claims[0].text == "Rule one `ls`." and len(claims) == 2


def test_a_bom_or_an_empty_block_is_frontmatter_too():
    assert [c.text for c in claims_from_guidance("\ufeff---\nname: x\n---\nAlways run `ls` here.\n")] == ["Always run `ls` here."]
    assert [c.text for c in claims_from_guidance("---\n---\nAlways run `ls` here.\n")] == ["Always run `ls` here."]


def test_an_empty_block_ends_at_its_own_closing_line():
    claims = claims_from_guidance("---\n---\nAlways run `ls` here.\n\nNever do X.\n\n---\nNever run `rm` here.\n")
    assert claims[0].text == "Always run `ls` here." and "Never run `rm` here." in claims[-1].text


def test_frontmatter_ends_at_its_first_closing_line():
    claims = claims_from_guidance("---\nname: x\n---\nAlways run `ls` here.\n\n---\n\nNever run `rm` here.\n")
    assert [c.text for c in claims] == ["Always run `ls` here.", "Never run `rm` here."]


def test_crlf_frontmatter_is_skipped():
    claims = claims_from_guidance("---\r\nname: x \r\n---\r\nAlways run `ls` here.\r\n")
    assert [c.text for c in claims] == ["Always run `ls` here."]
