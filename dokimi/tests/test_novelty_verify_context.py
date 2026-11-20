# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The faithfulness check shows the verifier the heading and lead-in of the cited spans (#1009)."""
from __future__ import annotations

from dokimi.novelty.atoms import Atom, verify_atoms
from dokimi.novelty.spans import segment

DOC = """# dpnp

## Purpose

dpnp is a NumPy-compatible library for Intel devices. Prefer this over plain NumPy when the arrays are large.

## When to use

Use `dpnp` for:

- Large arrays (>10,000 elements)
- Elementwise math

- Not related.
"""


class _Capture:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def json_object(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        return {"verdict": "supported", "why": "x"}


def _atom(spans, text_part, *more):
    ids = [s.id for s in spans if any(p in s.text for p in (text_part, *more))]
    return Atom(statement="s", question="q", answer="a", span_ids=tuple(ids))


def _blocks(prompt: str) -> tuple[str, str]:
    assert "\nCONTEXT" in prompt, "the faithfulness prompt has no CONTEXT block"
    source = prompt.split("SOURCE:\n", 1)[1].split("\n\nCONTEXT", 1)[0]
    context = prompt.split("never supplies the claim, so judge the SOURCE alone):\n", 1)[1]
    return source, context.split("\n\nAnswer with JSON", 1)[0]


def _check(atom, spans):
    client = _Capture()
    tally = verify_atoms([atom], spans, client=client)
    assert tally["supported"] == 1
    return _blocks(client.prompts[0])


def test_a_list_item_gets_its_lead_in_and_heading():
    spans = segment(DOC)
    source, context = _check(_atom(spans, "Elementwise math"), spans)
    assert source == "- Elementwise math"
    assert context == "Heading: dpnp > When to use\nLead-in: Use `dpnp` for:"


def test_a_sentence_gets_the_sentence_before_it():
    spans = segment(DOC)
    source, context = _check(_atom(spans, "Prefer this over"), spans)
    assert source.startswith("Prefer this over plain NumPy")
    assert "Lead-in: dpnp is a NumPy-compatible library for Intel devices." in context
    assert "Heading: dpnp > Purpose" in context


def test_a_span_with_no_heading_and_no_predecessor_gets_none():
    spans = segment("Setting 1 defaults to 10.")
    _, context = _check(_atom(spans, "Setting 1"), spans)
    assert context == "(none)"


def test_an_atom_citing_no_known_span_gets_none():
    spans = segment(DOC)
    atom = Atom(statement="s", question="q", answer="a", span_ids=("missing",))
    _, context = _check(atom, spans)
    assert context == "(none)"


def test_a_cited_context_span_is_not_repeated():
    spans = segment(DOC)
    atom = _atom(spans, "Large arrays", "Use `dpnp` for:", "Elementwise")
    source, context = _check(atom, spans)
    assert source.splitlines() == ["Use `dpnp` for:", "- Large arrays (>10,000 elements)",
                                   "- Elementwise math"]
    assert context == "Heading: dpnp > When to use"


def test_the_source_holds_only_the_cited_text():
    spans = segment(DOC)
    source, context = _check(_atom(spans, "Large arrays"), spans)
    assert source == "- Large arrays (>10,000 elements)"
    assert "Elementwise" not in source + context


def test_a_table_row_gets_the_header_row_of_its_table():
    spans = segment("# T\n\n## Flags\n\n| Flag | Default |\n|---|---|\n| --x | 10 |\n")
    source, context = _check(_atom(spans, "--x"), spans)
    assert source == "| --x | 10 |"
    assert context.splitlines()[-1] == "Table header: | Flag | Default |"


def test_a_cited_header_row_is_not_repeated_as_table_header():
    spans = segment("## Flags\n\n| Flag | Default |\n|---|---|\n| --x | 10 |\n")
    _, context = _check(_atom(spans, "| Flag |", "--x"), spans)
    assert "Table header" not in context


def test_the_lead_in_never_comes_from_the_previous_section():
    spans = segment("# T\n\n## H2\n\nLast sentence of H2.\n\n## H3\n\nText.\n")
    _, context = _check(_atom(spans, "## H3"), spans)
    assert context == "Heading: T > H3"
    _, context = _check(_atom(spans, "Text."), spans)
    assert context == "Heading: T > H3"
