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


_FRONT = "---\nname: s\ndescription: Use when operating the valve.\n---\n\n"


def test_a_setext_heading_is_one_heading_span_like_its_atx_form():
    """#974: `Valve setup. Quick reference` over `====` is not split into sentences."""
    atx = segment("# Valve setup. Quick reference\n\nThe valve opens clockwise.\n")
    setext = segment("Valve setup. Quick reference\n============================\n\n"
                     "The valve opens clockwise.\n")
    assert [s.kind for s in setext] == [s.kind for s in atx] == ["heading", "sentence"]
    assert setext[1].heading_path == ("Valve setup. Quick reference",)


def test_a_dash_underline_is_a_level_two_setext_heading():
    """#974: a `---` underline makes a level-two heading span, like `##`."""
    spans = segment("# Top\n\nSub title. More\n---\n\nBody text.\n")
    assert [s.kind for s in spans] == ["heading", "heading", "sentence"]
    assert spans[2].heading_path == ("Top", "Sub title. More")


def test_front_matter_closing_dashes_do_not_underline_the_last_field():
    """#974: the closing `---` of front matter is not a Setext underline."""
    spans = segment(_FRONT + "The valve opens clockwise.\n")
    assert all(s.kind != "heading" for s in spans)
    assert segment("---\nname: s\n---\n\nHi there.\n")[0].kind != "heading"


def test_dashes_under_a_field_line_that_is_not_front_matter_underline_it():
    """#974: only a leading `---` opens front matter; `a: b` over `---` is a heading."""
    spans = segment("a: b\n---\n\nBody.\n")
    assert [s.kind for s in spans] == ["heading", "sentence"]
    assert spans[1].heading_path == ("a: b",)
    assert segment("") == []


def test_a_setext_heading_or_indented_code_may_follow_a_closed_fence_directly():
    """#974: a closing fence ends a block, so the next line may start one."""
    spans = segment("```\nexample\n```\nTitle. More\n=====\n\nBody.\n")
    assert [s.kind for s in spans] == ["code", "heading", "sentence"]
    assert spans[2].heading_path == ("Title. More",)
    assert [s.kind for s in segment("```\nx\n```\n    code. More\n")] == ["code", "code"]


def test_a_fence_after_a_list_continuation_and_indented_code_is_seen_alike():
    """#974: `dangling_fence` reads a list item's continuation as `segment` does."""
    text = "- item\n  continuation\n\n    ```\n    example\n"
    assert [s.kind for s in segment(text)] == ["list_item", "code"]  # the fence is open in a list
    assert dangling_fence(text) == "```"
    assert dangling_fence("- item\n  continuation\n```\nx\n") == "```"


def test_indented_code_with_many_blank_lines_is_one_span():
    """#974: a long indented block full of blank lines is one span, found in linear time."""
    body = "    a\n" + "\n" * 3000 + "    b\n"
    spans = segment("Intro.\n\n" + body + "\nAfter.\n")
    assert [s.kind for s in spans] == ["sentence", "code", "sentence"]


class _CountingLine(str):
    """A line that counts how often its text is inspected."""

    reads = 0

    def strip(self, *args):
        _CountingLine.reads += 1
        return super().strip(*args)


def test_indented_code_end_does_bounded_work_over_many_blank_lines():
    """#974: a run of n blank lines is skipped once, not rescanned from each blank line."""
    from dokimi.novelty.spans import _indented_code_end

    n = 2000
    lines = [_CountingLine(row) for row in ["    a\n"] + ["\n"] * n + ["    b\n"]]
    _CountingLine.reads = 0
    assert _indented_code_end(lines, 0, False, 0) == len(lines)
    assert _CountingLine.reads < 4 * len(lines)


def test_a_rule_after_a_blank_line_or_a_list_item_is_not_a_setext_underline():
    """#974: `---` after a blank line or a list item is no Setext underline."""
    assert [s.kind for s in segment("Text here.\n\n---\n\nMore text.\n")] == [
        "sentence", "sentence", "sentence"]
    assert [s.kind for s in segment("- item\n---\n")] == ["list_item", "sentence"]


def test_four_space_indented_code_is_one_code_span_like_a_fence():
    """#974: `Open the valve. Close the valve.` indented four spaces is not two sentences."""
    fenced = segment("Intro.\n\n```\nOpen the valve. Close the valve.\n```\n\nEnd.\n")
    indented = segment("Intro.\n\n    Open the valve. Close the valve.\n\nEnd.\n")
    assert [s.kind for s in indented] == [s.kind for s in fenced] == ["sentence", "code", "sentence"]
    assert indented[1].text == "Open the valve. Close the valve."


def test_indented_code_runs_over_blank_lines_and_a_tab_opens_it():
    """#974: indented code is one span over blank lines, and a tab opens it."""
    spans = segment("Intro.\n\n\tone\n\n\ttwo\n\nEnd.\n")
    assert [s.kind for s in spans] == ["sentence", "code", "sentence"]
    assert "one" in spans[1].text and "two" in spans[1].text


def test_an_indented_line_is_not_code_inside_a_list_or_a_paragraph():
    """#974: an indented line in a paragraph or a list stays prose."""
    paragraph = segment("Intro text.\n    Still the paragraph.\n")
    assert [s.kind for s in paragraph] == ["sentence", "sentence"]
    assert [s.kind for s in segment("- item\n\n    More of the item.\n")] == ["list_item", "sentence"]


def test_indented_code_at_the_start_of_the_text_is_code():
    """#974: indented code at the start of the text is a code span."""
    assert [s.kind for s in segment("    x = 1\n")] == ["code"]


def test_segment_offsets_hold_for_setext_and_indented_code():
    """#974: span offsets reconstruct the text for Setext and indented code."""
    text = "Title. Sub\n=====\n\n    code. line\n\nEnd here.\n"
    for span in segment(text):
        assert text[span.start:span.end] == span.text


def test_dangling_fence_agrees_with_segment_on_a_backtick_line_in_indented_code():
    """#974: a stitched bundle must not read an indented fence inside indented code as an opener."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    text = _stitch([BundleFile("SKILL.md", "entrypoint", "# Skill\n\n    example\n    ```"),
                    BundleFile("references/r.md", "reference", "Reference sentence.")])
    spans = segment(text)
    assert dangling_fence("# Skill\n\n    example\n    ```") is None
    assert not any("Reference sentence." in s.text and s.kind == "code" for s in spans)


def test_a_setext_heading_straight_after_an_atx_heading_is_a_heading():
    """#974: `Title` over `===` right under `# Top` is a heading span, as `# Title` would be."""
    spans = segment("# Top\nValve setup. Quick reference\n===\n\nBody.\n")
    assert [s.kind for s in spans] == ["heading", "heading", "sentence"]
    assert spans[2].heading_path == ("Valve setup. Quick reference",)


def test_indented_code_straight_after_a_heading_is_one_code_span():
    """#974: indented code under a heading needs no blank line, like a fence there."""
    spans = segment("# Setup\n    Open the valve. Close the valve.\n")
    assert [s.kind for s in spans] == ["heading", "code"]


def test_a_setext_heading_may_wrap_over_several_lines():
    """#974: `Valve setup.` / `Quick reference` over `===` is one heading span."""
    spans = segment("Valve setup.\nQuick reference\n===\n\nBody.\n")
    assert [s.kind for s in spans] == ["heading", "sentence"]
    assert spans[0].text == "Valve setup.\nQuick reference\n==="
    assert spans[1].heading_path == ("Valve setup. Quick reference",)


def test_indented_code_that_opens_with_backticks_ends_with_its_indentation():
    """#974: `    ```` after a blank line is indented code, not a fence that swallows the next text."""
    spans = segment("# Setup\n\n    ```\n    Open the valve. Close the valve.\n\nEnd.\n")
    assert [s.kind for s in spans] == ["heading", "code", "sentence"]
    assert dangling_fence("# Setup\n\n    ```\n    Open the valve. Close the valve.\n") is None


def test_a_heading_or_indented_code_may_follow_a_setext_heading_directly():
    """#974: a completed Setext heading ends its block, as an ATX heading does."""
    spans = segment("Top\n===\nSub title. More\n---\n\nBody.\n")
    assert [s.kind for s in spans] == ["heading", "heading", "sentence"]
    assert spans[2].heading_path == ("Top", "Sub title. More")
    spans = segment("Setup\n=====\n    Open the valve. Close the valve.\n")
    assert [s.kind for s in spans] == ["heading", "code"]


def test_a_bundle_that_starts_with_indented_code_keeps_it_one_code_span():
    """#974: `_stitch` must not strip the indentation that makes the first line code."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    text = _stitch([BundleFile("SKILL.md", "entrypoint", "    Open the valve. Close the valve.\n")])
    assert [s.kind for s in segment(text)] == ["code"]
    assert [s.kind for s in segment(_stitch([BundleFile("SKILL.md", "entrypoint", "\n\n  x\n")]))] \
        == ["sentence"]


def test_a_leading_rule_is_not_front_matter():
    """#974: `---` then a blank line is a rule; the later `---` can underline a title."""
    spans = segment("---\n\nTitle. More\n---\n\nBody.\n")
    assert [s.kind for s in spans] == ["sentence", "heading", "sentence"]
    assert spans[2].heading_path == ("Title. More",)


def test_an_atx_heading_may_be_indented_up_to_three_spaces():
    """#974: `  # Setup` is a heading, also as the first line of a stitched file."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    text = _stitch([BundleFile("SKILL.md", "entrypoint", "  # Setup\n\nBody.\n")])
    spans = segment(text)
    assert [s.kind for s in spans] == ["heading", "sentence"]
    assert spans[1].heading_path == ("Setup",)
    assert [s.kind for s in segment("    # Setup\n")] == ["code"]


def test_a_setext_underline_with_crlf_line_endings_is_an_underline():
    """#974: `Setup\\r\\n=====\\r\\n` is one heading and the stitched reference stays apart."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    source = "Setup\r\n=====\r\n    ```\r\n    example\r\n"
    assert [s.kind for s in segment(source)] == ["heading", "code"]
    assert dangling_fence(source) is None
    text = _stitch([BundleFile("SKILL.md", "entrypoint", source),
                    BundleFile("references/r.md", "reference", "Reference sentence.")])
    assert not any("Reference sentence." in s.text and s.kind == "code" for s in segment(text))


def test_a_bare_rule_is_not_a_completed_heading_for_the_next_indented_line():
    """#974: `---` then `example` then indented backticks: `segment` sees a paragraph, not code."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    source = "---\n    example\n    ```\n    content\n"
    assert dangling_fence(source) == "```"
    text = _stitch([BundleFile("SKILL.md", "entrypoint", source),
                    BundleFile("references/r.md", "reference", "Reference sentence.")])
    spans = segment(text)
    assert not any("Reference sentence." in s.text and s.kind == "code" for s in spans)
    assert any(s.text == "Reference sentence." for s in spans)


def test_an_indented_empty_atx_heading_is_read_alike_by_segment_and_dangling_fence():
    """#974: `  #` plus a line ending is no heading in either, so both see the same fence."""
    from dokimi.novelty.bundle import BundleFile, _stitch

    source = "  #\n    ```\n    example\n"
    assert segment(source)[0].kind == "sentence"
    assert dangling_fence(source) == "```"
    spans = segment(_stitch([BundleFile("SKILL.md", "entrypoint", source),
                             BundleFile("references/r.md", "reference", "Reference sentence.")]))
    assert not any("Reference sentence." in s.text and s.kind == "code" for s in spans)
    assert any(s.text == "Reference sentence." for s in spans)
