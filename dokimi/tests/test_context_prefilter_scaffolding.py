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
