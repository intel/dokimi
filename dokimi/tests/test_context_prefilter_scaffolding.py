# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""trace prefilter: Markdown scaffolding is metadata, not a claim (#973)."""

from dokimi.context.prefilter import claims_from_guidance


def test_markdown_scaffolding_is_not_a_claim():
    """#973: a Setext heading, an indented code block and an HTML comment are not rules (docs/session-prefilter.md)."""
    rule = "Keep the changelog fragment current."
    for text in (f"Release checklist\n=================\n\n- {rule}\n",
                 f"# Guide\n\n{rule}\n\n    towncrier create 123.fix.md\n",
                 f"<!-- Copyright (C) 2026\nSPDX-License-Identifier: Apache-2.0 -->\n\n- {rule}\n"):
        claims = claims_from_guidance(text)
        assert [c.text for c in claims] == [f"- {rule}" if text.startswith(("Rel", "<!")) else rule]
        assert not any(c.protection for c in claims)


def test_thematic_break_and_comment_suffix_keep_the_instruction():
    """#973: a `***` rule, a fence or a `#` after `-->` must not swallow `Never delete production data.`"""
    rule = "Never delete production data."
    for text in (f"{rule}\n***\nRelease checklist\n===\n", f"<!-- note --> ```\n\n{rule}\n", f"<!-- note --> # {rule}\n"):
        assert [c.protection for c in claims_from_guidance(text) if rule in c.text] == [("negation",)], text


def test_inline_comment_closing_after_a_blank_line_is_not_a_protected_claim():
    """#973: `Apache-2.0` in a comment that spans a blank line is not a `version_gate` claim."""
    for text in ("Keep it. <!-- note\nApache-2.0\n\n-->\n", "- Keep it. <!-- note\nApache-2.0\n\n-->\n"):
        assert not any(c.protection for c in claims_from_guidance(text)), text


def test_a_comment_carried_over_a_blank_line_adds_nothing_to_the_rule_before_it():
    """#973: a command and a word inside `<!-- ... -->` opened after a rule are not that rule's command or keyword."""
    for prefix in ("", "- "):
        claims = claims_from_guidance(f"{prefix}Maintain changelog. <!-- zebra\n\n`make release`\n-->\n")
        assert [c.commands for c in claims] == [()]
        assert "zebra" not in claims[0].keywords


def test_a_prohibition_cut_in_two_by_a_comment_stays_protected_in_both_claims():
    """#973: `- Never <!-- note ... --> delete production data.` gives no unprotected `delete production data.` claim."""
    claims = claims_from_guidance("- Never <!-- note\n\n--> delete production data.\n")
    assert [c.protection for c in claims if "delete" in c.text] == [("negation",)]


def test_a_prohibition_cut_by_two_comments_stays_protected_in_every_claim():
    """#973: `- Never <!-- first ... --> ever <!-- second --> delete production data.` leaves no unprotected claim."""
    claims = claims_from_guidance("- Never <!-- first\n\n--> ever <!-- second --> delete production data.\n")
    assert [c.protection for c in claims if "delete" in c.text] == [("negation",)]


def test_an_escaped_comment_opener_is_visible_text_and_keeps_its_negation():
    for text in ("Restart the package. \\<!-- Never delete data. -->", "Restart the package. \\\\\\<!-- Never delete data. -->"):
        claims = [c for c in claims_from_guidance(text) if "Never delete data" in c.text]
        assert [c.protection for c in claims] == [("negation",)], text


def test_a_comment_after_an_escaped_backslash_is_still_a_comment():
    claims = claims_from_guidance("Restart the package. \\\\<!-- Never delete data. -->")
    assert [c.protection for c in claims if "Restart" in c.text] == [()]
