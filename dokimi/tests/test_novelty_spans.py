# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Segmentation, protection detection, and span statistics — all offline."""

from __future__ import annotations

from dokimi.novelty.atoms import Atom
from dokimi.novelty.spans import (
    dangling_fence,
    index_atoms,
    protection_tags,
    segment,
    span_summary,
)

DOC = """\
# Setup

Install the runtime first. Do not enable the legacy driver.

## Flags

- `--fast` speeds up startup
- `--strict` requires version 2.4 or newer

```bash
make build
```

| flag | default |
| --- | --- |
| --fast | off |
"""


def test_segment_offsets_reconstruct_the_source():
    spans = segment(DOC)
    for span in spans:
        assert DOC[span.start:span.end] == span.text


def test_segment_keeps_a_fenced_block_whole():
    code = [s for s in segment(DOC) if s.kind == "code"]
    assert len(code) == 1
    assert "make build" in code[0].text


_NESTED = "# Valve setup\n\nThe valve opens clockwise.\n\n{o}markdown\n```\nOpen the valve. Close the valve.\n```\n{o}\n\nCheck the gauge.\n"


def test_longer_fence_keeps_an_inner_shorter_fence_in_one_code_span():
    """#974: a four-backtick fence is not closed by a three-backtick line inside it."""
    kinds = ["heading", "sentence", "code", "sentence"]
    assert [s.kind for s in segment(_NESTED.format(o="~~~~"))] == kinds
    spans = segment(_NESTED.format(o="````"))
    assert [s.kind for s in spans] == kinds
    assert spans[2].text.count("```") == 2 + 2


def test_a_longer_closing_fence_still_closes():
    """#974: a closing fence at least as long as the opener closes the block."""
    spans = segment("```\ncode\n`````\n\nAfter the block.\n")
    assert [s.kind for s in spans] == ["code", "sentence"]


def test_a_shorter_closing_fence_does_not_close():
    """#974: a closing line shorter than the opening run is content."""
    spans = segment("````\ncode\n```\nstill code\n")
    assert [s.kind for s in spans] == ["code"]


def test_a_fence_line_with_text_after_it_does_not_close():
    """A closing fence has nothing after its run: ```` ```python ```` is content."""
    spans = segment("```\nx\n```python\nNot code. Really.\n")
    assert [s.kind for s in spans] == ["code"]
    assert dangling_fence("```\nx\n```python\n") == "```"
    assert dangling_fence("~~~\nx\n~~~ note\n") == "~~~"
    assert dangling_fence("```\nx\n```  \n") is None


def test_a_backtick_in_the_info_string_does_not_open_a_fence():
    """CommonMark: a backtick opener's info string has no backtick, so this is prose."""
    assert dangling_fence("``` `x` ```\nHi there.") is None
    assert [s.kind for s in segment("``` `x` ```\nHi there.\n")] == ["sentence"]
    assert dangling_fence("~~~ `x`\nstill code\n") == "~~~"


def test_dangling_fence_reads_the_whole_run():
    """#974: `dangling_fence` uses the same whole-run rule as `segment`."""
    assert dangling_fence("````\n```\ncode\n```\n") == "````"
    assert dangling_fence("````\n```\ncode\n````\n") is None
    assert dangling_fence("") is None


def test_heading_path_tracks_nesting():
    spans = segment(DOC)
    flags = [s for s in spans if s.kind == "list_item"]
    assert flags
    assert flags[0].heading_path == ("Setup", "Flags")


def test_protection_detects_negation_and_version_gates():
    assert "negation" in protection_tags("Do not enable the legacy driver.")
    assert "version_gate" in protection_tags("Requires version 2.4 or newer.")
    assert "threshold" in protection_tags("Fails above 80% utilization.")
    assert protection_tags("It speeds up startup.") == ()


def test_span_summary_counts_tokens_carrying_nothing():
    spans = segment(DOC)
    atom = Atom(
        statement="The legacy driver must not be enabled.",
        question="Should the legacy driver be enabled?",
        answer="No.",
        span_ids=(spans[1].id,),
    )
    index_atoms(spans, [atom])
    summary = span_summary(spans, [atom])
    assert summary["n"] == len(spans)
    assert summary["sole_carrier"] == 1
    # Every span but the one carrying the atom is unattributed, so the upper bound on
    # waste is high — that is the point of calling it a bound.
    assert 0.0 < summary["frac_carrying_nothing"] < 1.0
    assert summary["by_kind"]["code"] == 1
    assert summary["n_atoms_indexed"] == 1
    assert summary["capped_atom_set"] is False


def test_span_summary_indexes_every_atom_so_its_atom_set_is_never_capped():
    """A cap does not make the dead-token share noisy, it makes it wrong (0.064 read 0.743 in
    the study), so every run indexes all its extracted atoms and writes `capped_atom_set: false`;
    the key stays because the gate still reads `true` on artifacts written before #371."""
    spans = segment(DOC)
    atoms = [
        Atom(statement=f"claim {i}", question="q", answer="a", span_ids=(spans[1].id,))
        for i in range(3)
    ]
    summary = span_summary(spans, atoms)
    assert (summary["capped_atom_set"], summary["n_atoms_indexed"]) == (False, 3)
    assert span_summary(spans)["n_atoms_indexed"] is None


def _kinds(text):
    return [(s.kind, s.text) for s in segment(text)]


def test_setext_heading_is_a_heading_and_sets_the_path():
    """#973: a line underlined by `===` or `---` is a heading, as `# ` is (docs/session-prefilter.md)."""
    spans = segment("Release checklist\n=================\n\nKeep it.\n\nSteps\n-----\n\n- Do it now.\n")
    assert [(s.kind, s.text) for s in spans[:2]] == [
        ("heading", "Release checklist\n================="), ("sentence", "Keep it.")]
    assert spans[1].heading_path == ("Release checklist",)
    assert (spans[2].kind, spans[3].heading_path) == ("heading", ("Release checklist", "Steps"))


def test_setext_underline_is_not_taken_from_a_rule_a_list_or_frontmatter():
    """#973: `---` after a list item, a lone `-`, a rule after a rule and a frontmatter closer underline nothing."""
    assert _kinds("- item\n---\n")[0] == ("list_item", "- item")
    assert [k for k, _ in _kinds("---\n\n---\n")] == ["sentence", "sentence"]
    assert _kinds("Text\n-\n")[0][0] != "heading"
    front = segment("---\nname: x\n---\n\nKeep it.\n")
    assert "heading" not in [s.kind for s in front]


def test_indented_block_after_a_blank_line_is_code():
    """#973: four spaces or a tab after a blank line is a code block, one span for the whole block."""
    spans = segment("Keep it.\n\n    make build\n\n    make test\n\nDone.\n")
    assert [(s.kind, s.text) for s in spans] == [
        ("sentence", "Keep it."), ("code", "make build\n\n    make test"), ("sentence", "Done.")]
    assert segment("\tmake build\n")[0].kind == "code"


def test_indented_line_is_not_code_in_a_paragraph_or_after_a_list_item():
    """#973: an indented line continuing a paragraph or a list item stays prose."""
    assert [s.kind for s in segment("Keep it\n    going.\n")] == ["sentence"]
    assert [s.kind for s in segment("- item\n\n    more of the item.\n")] == ["list_item", "sentence"]
    assert [s.kind for s in segment("    ")] == []


def test_html_comment_is_one_comment_span():
    """#973: `<!--` to `-->`, over several lines or one, is a `comment` span with exact offsets."""
    text = "<!-- Copyright\nSPDX-License-Identifier: Apache-2.0 -->\n\nKeep it.\n\n<!-- one -->\n"
    spans = segment(text)
    assert [(s.kind, s.protection) for s in spans] == [("comment", ()), ("sentence", ()), ("comment", ())]
    assert segment("<!-- Apache-2.0 -->")[0].protected is False
    assert all(text[s.start:s.end] == s.text for s in spans)


def test_unclosed_html_comment_is_not_swallowed():
    """#973: without `-->` the opener is read as prose and the text after it is still segmented."""
    assert [k for k, _ in _kinds("<!-- never closed\n\n- Keep it.\n")] == ["sentence", "list_item"]


def test_a_comment_ends_a_paragraph():
    """#973: a comment line interrupts a paragraph, as a heading or a list item does."""
    assert [k for k, _ in _kinds("Keep it.\n<!-- note -->\nThen go.\n")] == ["sentence", "comment", "sentence"]


def test_prior_to_is_an_ordering():
    """#973: `prior to` orders two steps like `before` (docs/skill-novelty.md, amber `protected`)."""
    assert protection_tags("Load the configuration prior to starting the server.") == ("ordering",)
    assert protection_tags("Load the configuration before starting the server.") == ("ordering",)
    assert protection_tags("The prior art is in the tracker.") == ()


def test_setext_heading_replaces_a_sibling_in_the_path():
    """#973: a level 2 Setext heading replaces the previous level 2 heading, it does not nest under it."""
    spans = segment("Top\n===\n\nA\n--\n\nB\n--\n\nBody text.\n")
    assert [s.kind for s in spans] == ["heading", "heading", "heading", "sentence"]
    assert spans[-1].heading_path == ("Top", "B")


def test_frontmatter_closer_underlines_the_last_key_of_a_block_with_a_blank_line():
    """#973: the closing `---` of an opening frontmatter block is not a Setext underline."""
    spans = segment("---\nname: x\n\ndescription: y\n---\n\nKeep it.\n")
    assert "heading" not in [s.kind for s in spans]
    assert [s.kind for s in segment("Intro\n\nTitle\n---\n")].count("heading") == 1


def test_scaffolding_at_the_end_of_the_text_without_a_newline():
    """#973: a code block or a comment on the last line, with no line break, is whole."""
    assert [(s.kind, s.text) for s in segment("Keep it.\n\n    make build")] == [
        ("sentence", "Keep it."), ("code", "make build")]
    assert [(s.kind, s.text) for s in segment("<!-- x -->")] == [("comment", "<!-- x -->")]
    assert segment("") == []


def test_guidance_after_a_closing_comment_marker_is_still_a_rule():
    """#973: text after `-->` on the closing line is prose, with its own protection tags."""
    text = "<!-- maintenance note --> Never delete production data.\n"
    spans = segment(text)
    assert [(s.kind, s.text) for s in spans] == [
        ("comment", "<!-- maintenance note -->"), ("sentence", "Never delete production data.")]
    assert spans[1].protection == ("negation",)
    assert all(text[s.start:s.end] == s.text for s in spans)
    multi = segment("<!-- a\nb --> Keep it.\n<!-- c --> <!-- d -->\n")
    assert [s.kind for s in multi] == ["comment", "sentence", "comment", "comment"]


def test_setext_heading_keeps_its_underline_in_the_span_text():
    """#973: `render` rebuilds a candidate from span text, so a kept heading must still read as a heading."""
    from dokimi.novelty.shorten import render

    spans = segment("Setup\n===\n\nInstall runtime.\n")
    assert render(spans, [s.id for s in spans]) == "Setup\n===\n\nInstall runtime."
    assert spans[0].heading_path == ("Setup",)


def test_an_indented_example_that_starts_with_a_list_marker_is_code():
    """#973: the four-space rule also holds for a line that looks like a list item or a table row."""
    spans = segment("Guide.\n\n    - Never delete production data.\n    | a | b |\n")
    assert [(s.kind, s.protection) for s in spans] == [("sentence", ()), ("code", ("negation",))]
    nested = segment("- item\n\n    - nested item\n")
    assert [s.kind for s in nested] == ["list_item", "list_item"]


def test_a_setext_heading_may_span_several_lines():
    """#973: a paragraph of several lines over an underline is one heading and sets the path."""
    spans = segment("Release\nchecklist\n=========\n\n- Keep the changelog current.\n")
    assert [(s.kind, s.text) for s in spans] == [
        ("heading", "Release\nchecklist\n========="), ("list_item", "- Keep the changelog current.")]
    assert spans[1].heading_path == ("Release checklist",)
    assert [s.kind for s in segment("- item\n---\n")] == ["list_item", "sentence"]


def test_a_blockquote_over_a_rule_is_not_a_setext_heading():
    """#973: a `>` line opens no Setext heading, so a quoted rule above `---` stays a rule."""
    spans = segment("> Never delete production data.\n---\n")
    assert [s.kind for s in spans] == ["sentence", "sentence"]
    assert spans[0].protection == ("negation",)
    assert [s.kind for s in segment("Intro\n> quoted\n===\n")].count("heading") == 0


def test_prior_to_wrapped_across_lines_is_still_an_ordering():
    """#973: a line break between `prior` and `to` is a space in Markdown."""
    assert segment("Load configuration prior\nto starting the server.\n")[0].protection == ("ordering",)


def test_a_thematic_break_is_not_a_setext_underline_or_part_of_a_paragraph():
    """#973: `***` / `___` / `_ _ _` end the paragraph above them; the instruction stays a sentence."""
    for eol in ("\n", "\r\n"):
        for rule in ("***", "___", "_ _ _"):
            spans = segment(f"Never delete production data.{eol}{rule}{eol}Release checklist{eol}==={eol}")
            assert [(s.kind, s.text) for s in spans] == [
                ("sentence", "Never delete production data."), ("heading", f"Release checklist{eol}==="),
            ], (rule, eol)
            assert spans[0].protection == ("negation",)


def test_text_after_a_closing_comment_marker_is_prose_not_a_block_opener():
    """#973: `<!-- note --> ```` is not a fence and `<!-- note --> # Never ...` is not a heading."""
    fenced = segment("<!-- note --> ```\n\nNever delete production data.\n")
    assert [(s.kind, s.text) for s in fenced] == [
        ("comment", "<!-- note -->"), ("sentence", "```"), ("sentence", "Never delete production data.")]
    hashed = segment("<!-- note --> # Never delete production data.\n")
    assert [(s.kind, s.text) for s in hashed] == [
        ("comment", "<!-- note -->"), ("sentence", "# Never delete production data.")]
    assert hashed[1].protection == ("negation",)


def test_a_comment_inside_a_sentence_or_list_item_is_not_read_as_a_rule():
    """#973: `Apache-2.0` inside an inline `<!-- -->` is not a `version_gate`; the text around it still is read."""
    for text in ("Keep the changelog current. <!-- Apache-2.0 -->\n", "- Keep the changelog current. <!-- Apache-2.0 -->\n",
                 "- Keep the changelog current.\n  <!-- Apache-2.0 -->\n"):
        spans = segment(text)
        assert [s.protection for s in spans] == [()], text
        assert all(text[s.start:s.end] == s.text for s in spans)
    assert segment("Never skip it. <!-- 2.0 -->\n")[0].protection == ("negation",)


def test_a_comment_holding_a_sentence_boundary_is_not_split_or_read_as_a_rule():
    """#973: `note. Apache-2.0` inside one `<!-- -->` stays in the sentence that holds the comment."""
    text = "Keep the changelog current. <!-- note. Apache-2.0 -->\n\nNever skip it.\n"
    spans = segment(text)
    assert [s.text for s in spans] == [
        "Keep the changelog current. <!-- note. Apache-2.0 -->", "Never skip it."]
    assert [s.protection for s in spans] == [(), ("negation",)]
    assert all(text[s.start:s.end] == s.text for s in spans)


def test_an_inline_comment_closing_after_a_blank_line_stays_one_span_without_protection():
    """#973: `Apache-2.0` in a comment opened mid-paragraph or mid-list-item is not a `version_gate`, blank line or not.

    The comment is a span of its own, so the rule before it does not carry its text.
    """
    for text in ("Keep it. <!-- note\nApache-2.0\n\n-->\n", "- Keep it. <!-- note\nApache-2.0\n\n-->\n"):
        spans = segment(text)
        assert [s.protection for s in spans] == [(), ()], text
        assert [s.kind for s in spans][1] == "comment"
        assert spans[1].text == "<!-- note\nApache-2.0\n\n-->"
        assert all(text[s.start:s.end] == s.text for s in spans)


def test_a_literal_comment_marker_in_inline_code_opens_no_comment():
    """#973: `` `<!--` `` and `` `-->` `` in inline code hide nothing: the rule between them keeps its tag."""
    text = "Use `<!--` to open comments.\n\nNever delete production data.\n\nUse `-->` to close comments.\n"
    spans = segment(text)
    assert [s.text for s in spans if "Never" in s.text] == ["Never delete production data."]
    assert [s.protection for s in spans if "Never" in s.text] == [("negation",)]
    one = segment("Use `<!--` and `-->` here. Never delete production data.\n")
    assert any(s.protection == ("negation",) for s in one)


def test_a_literal_comment_marker_in_double_backtick_code_opens_no_comment():
    """#973: ``` ``<!--`` ``` and ``` ``-->`` ``` are inline code spans, not empty spans around a comment marker."""
    text = "Use ``<!--`` to open comments.\n\nNever delete production data.\n\nUse ``-->`` to close comments.\n"
    spans = segment(text)
    assert [s.protection for s in spans if "Never" in s.text] == [("negation",)]
    assert any(s.protection == ("negation",) for s in segment("Use ``<!--`` and ``-->``. Never delete production data.\n"))


def test_a_code_span_wrapped_over_a_line_break_opens_no_comment():
    """#973: `` `<!--\nexample` `` is one code span, so the rule after it keeps its `ordering` tag, in a paragraph and a list item."""
    for text in ("Use `<!--\nexample` before starting and `-->` here.\n",
                 "- Use `<!--\n  example` before starting and `-->` here.\n"):
        spans = segment(text)
        assert [t for s in spans for t in s.protection] == ["ordering"], text
        assert [s.kind for s in spans].count("comment") == 0
        assert all(text[s.start:s.end] == s.text for s in spans)


def test_a_comment_opened_after_the_closer_of_another_runs_to_its_own_closer():
    """#973: `Apache-2.0` between `<!-- second` and its `-->` gets no `version_gate`, in a paragraph and a list item."""
    for text in ("Keep it. <!-- first\n\n--> <!-- second\nApache-2.0\n\n-->\n",
                 "- Keep it. <!-- first\n\n--> <!-- second\nApache-2.0\n\n-->\n"):
        spans = segment(text)
        assert not [s for s in spans if s.kind != "comment" and "Apache-2.0" in s.text], text
        assert not [s for s in spans if s.protection], text
        assert all(text[s.start:s.end] == s.text for s in spans)


def test_a_sentence_cut_falls_in_no_code_span():
    """#973: `` `example. A <!--` `` is one code span, so `before starting` after it keeps its `ordering` tag."""
    text = "Use `example. A <!--` before starting and `-->` here.\n"
    spans = segment(text)
    assert [t for s in spans for t in s.protection] == ["ordering"]
    assert [s.text for s in spans] == [text.strip()]


def test_a_rule_cut_in_two_by_a_comment_keeps_its_protection_on_both_halves():
    """#973: `Never <!-- note\\n\\n--> delete production data.` is one prohibition: `delete production data.` keeps `negation`."""
    for prefix in ("", "- "):
        text = f"{prefix}Never <!-- note\n\n--> delete production data.\n"
        spans = [s for s in segment(text) if s.kind != "comment"]
        assert [s.text for s in spans] == [f"{prefix}Never", "delete production data."]
        assert [s.protection for s in spans] == [("negation",), ("negation",)]
        assert all(text[s.start:s.end] == s.text for s in segment(text))


def test_a_rule_cut_by_two_comments_keeps_its_protection_on_every_part():
    """#973: `Never <!-- first --> ever <!-- second --> delete production data.` is one prohibition, in all three parts."""
    for prefix in ("", "- "):
        text = f"{prefix}Never <!-- first\n\n--> ever <!-- second --> delete production data.\n"
        spans = [s for s in segment(text) if s.kind != "comment"]
        assert [s.text for s in spans] == [f"{prefix}Never", "ever", "delete production data."]
        assert [s.protection for s in spans] == [("negation",)] * 3


def test_a_comment_between_two_sentences_does_not_carry_protection_to_the_next():
    """#973: `Never delete data. <!-- n --> Restart the service.` is two sentences; only the first is a prohibition."""
    for prefix in ("", "- "):
        # a list item is one span, so only the comment that outruns it separates the two
        for text in ((f"{prefix}Never delete data. <!-- n --> Restart the service.\n",) * (not prefix)
                     + (f"{prefix}Never delete data. <!-- note\n\n--> Restart the service.\n",)):
            spans = [s for s in segment(text) if s.kind != "comment"]
            assert spans[0].text == f"{prefix}Never delete data.", text
            assert spans[-1].text.endswith("Restart the service."), text
            assert [s.protection for s in spans] == [("negation",), ()], text


def test_the_second_indented_paragraph_of_a_list_item_is_content_not_code():
    """#973: `    Never skip `make lint`.` after a list item's first continuation paragraph is a sentence, not `code`."""
    spans = segment("1. Step one\n\n    Run `make test` first.\n\n    Never skip `make lint`.\n")
    assert [(s.kind, s.protection) for s in spans] == [
        ("list_item", ()), ("sentence", ("ordering",)), ("sentence", ("negation",))]


def test_a_line_continuing_a_list_item_above_dashes_is_no_setext_heading():
    """#973: `without `approval`.` directly under `- Never run `deploy`` continues the item; `---` opens no heading."""
    spans = segment("- Never run `deploy`\nwithout `approval`.\n---\n\nKeep `CHANGELOG.md` current.\n")
    assert "heading" not in [s.kind for s in spans]
    assert spans[-1].heading_path == ()


def test_the_short_comments_close_at_once():
    """#973: `<!-->` and `<!--->` are complete comments, so the rule below them is not inside one."""
    for opener in ("<!-->", "<!--->"):
        spans = segment(f"{opener}\nNever delete data.\n<!-- y -->\n")
        assert [(s.kind, s.text) for s in spans] == [
            ("comment", opener), ("sentence", "Never delete data."), ("comment", "<!-- y -->")]


def test_an_indented_comment_does_not_break_the_paragraph_it_continues():
    """#973: `    <!-- x -->` (four spaces) is paragraph text, so `more\\n---` underlines the whole paragraph."""
    spans = segment("Text\n    <!-- x -->\nmore\n---\n")
    assert [s.kind for s in spans] == ["heading"]
