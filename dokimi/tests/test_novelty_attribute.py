# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Span attribution, the colour legend, and the two derived listings."""

from __future__ import annotations

from dokimi.novelty.atoms import Atom
from dokimi.novelty.attribute import (
    attribute_spans,
    attribution_summary,
    carrier_confirmation,
    dead_weight,
    sole_carriers,
    span_check_text,
    topic_rollup,
)
from dokimi.novelty.report import (
    render_annotated_html,
    render_annotated_markdown,
)
from dokimi.novelty.spans import Span, segment

DOC = """\
# Guide

Enable the accelerator with `--accel`. Do not enable it on version 1.0.

## Background

The project started as a research prototype and has many contributors.
"""


def _attributed(outcomes=None, categories=None):
    spans = segment(DOC)
    accel = next(s for s in spans if "--accel" in s.text)
    gate = next(s for s in spans if "Do not enable" in s.text)
    atoms = [
        Atom(statement="--accel enables the accelerator.", question="q1", answer="a",
             span_ids=(accel.id,)),
        Atom(statement="The accelerator must not be enabled on version 1.0.", question="q2",
             answer="a", span_ids=(gate.id,)),
    ]
    return spans, atoms, attribute_spans(
        spans, atoms, outcomes=outcomes or {}, categories=categories or {}
    )


def test_a_span_with_a_harmful_atom_is_red_even_when_it_also_helps():
    spans, atoms, _ = _attributed()
    shared = spans.index(next(s for s in spans if "--accel" in s.text))
    both = [
        Atom(statement=atoms[0].statement, question="q1", answer="a",
             span_ids=(spans[shared].id,)),
        Atom(statement="Conflicting claim.", question="q2", answer="a",
             span_ids=(spans[shared].id,)),
    ]
    rows = attribute_spans(
        spans, both,
        outcomes={both[0].id: "added"},
        categories={both[1].id: "harmful_conflicting"},
    )
    row = next(r for r in rows if r.span.id == spans[shared].id)
    assert row.label == "harmful"
    assert row.colour == "red"


def test_added_and_redundant_colour_apart():
    _spans, atoms, rows = _attributed()
    assert all(r.label != "added" for r in rows)  # no outcomes: nothing claims to be added
    spans, atoms, rows = _attributed(
        outcomes={atoms[0].id: "added", atoms[1].id: "redundant"}
    )
    labels = {r.span.id: r.label for r in rows}
    accel = next(s for s in spans if "--accel" in s.text)
    gate = next(s for s in spans if "Do not enable" in s.text)
    assert labels[accel.id] == "added"
    # The version gate stays amber: "the model already knew it" does not make the line
    # that stops a wrong default droppable.
    assert labels[gate.id] == "protected"


def test_prose_with_no_atoms_is_dead_weight_and_dim():
    _spans, _atoms, rows = _attributed()
    empty = dead_weight(rows)
    assert any("research prototype" in row["text"] for row in empty)
    assert all(r.colour == "dim" for r in rows if r.label == "empty")


def test_sole_carriers_are_listed_with_their_protection():
    _spans, _atoms, rows = _attributed()
    carriers = sole_carriers(rows)
    assert carriers
    gated = [row for row in carriers if row["protection"]]
    assert gated and "version_gate" in gated[0]["protection"]


def test_summary_token_shares_sum_to_one_and_carry_the_legend():
    _spans, _atoms, rows = _attributed()
    summary = attribution_summary(rows)
    assert abs(sum(summary["token_share_by_label"].values()) - 1.0) < 0.01
    assert "green" in summary["legend"]


def test_topic_rollup_groups_by_heading():
    _spans, _atoms, rows = _attributed()
    topics = topic_rollup(rows)
    headings = {tuple(t["heading_path"]) for t in topics}
    assert ("Guide", "Background") in headings


class _FakeOracle:
    """Recovers an atom only from texts listed for it; records what it was shown."""

    def __init__(self, recoverable: dict[str, set[str]] | None = None):
        self.recoverable = recoverable or {}
        self.seen: list[tuple[str, str]] = []

    def verdict(self, atom, text):
        self.seen.append((atom.id, text))
        needles = self.recoverable.get(atom.id, set())
        retained = any(needle in text for needle in needles)
        return type("V", (), {"retained": retained})()


def test_a_span_is_checked_with_its_heading_path_prepended():
    spans = segment(DOC)
    gate = next(s for s in spans if "Do not enable" in s.text)
    text = span_check_text(gate)
    assert text.startswith("Guide")          # the subject often lives in the heading
    assert "Do not enable" in text
    bare = Span(id="s9999", kind="sentence", text="Pin the driver.", start=0, end=15)
    assert span_check_text(bare) == bare.text   # no heading: nothing prepended


def test_an_atom_recoverable_from_its_cited_span_is_located():
    spans, atoms, _rows = _attributed()
    oracle = _FakeOracle({a.id: {"--accel"} for a in atoms[:1]} | {atoms[1].id: {"1.0"}})
    out = carrier_confirmation(spans, atoms, oracle=oracle)
    assert (out["n_atoms"], out["n_located"], out["n_unlocated"]) == (2, 2, 0)
    assert out["frac_located"] == 1.0
    assert out["n_pairs_checked"] == 2        # one cited span each, no wasted calls
    assert out["checked_with_heading_path"] is True


def test_an_atom_its_cited_span_does_not_carry_is_reported_unlocated():
    spans, atoms, _rows = _attributed()
    oracle = _FakeOracle({atoms[0].id: {"--accel"}})   # atoms[1] recoverable from nothing
    out = carrier_confirmation(spans, atoms, oracle=oracle)
    assert out["n_located"] == 1
    assert out["unlocated_atoms"] == [atoms[1].id]
    assert out["frac_located"] == 0.5
    # an unlocated atom is not a deletion candidate, and the payload has to say so
    assert "not necessarily wrong" in out["note"]


def test_confirmation_travels_with_the_labels_when_it_was_run():
    spans, atoms, rows = _attributed()
    assert "carrier_confirmation" not in attribution_summary(rows)
    out = carrier_confirmation(spans, atoms, oracle=_FakeOracle())
    summary = attribution_summary(rows, confirmation=out)
    assert summary["carrier_confirmation"]["frac_located"] == 0.0


def test_views_render_every_span_and_escape_html():
    _spans, _atoms, rows = _attributed()
    html = render_annotated_html(rows, title="t & <b>")
    assert "t &amp; &lt;b&gt;" in html
    assert html.count("<span") >= len([r for r in rows if r.span.text.strip()])
    markdown = render_annotated_markdown(rows, title="t")
    assert "--accel" in markdown
    assert "**green**" in markdown  # the legend travels with the view


def test_an_unjudged_atom_gets_no_fallback_label():
    spans, atoms, _ = _attributed()
    accel = next(s for s in spans if "--accel" in s.text)
    # Classified run, but the value judge never answered for this atom (no category).
    labels = {r.span.id: r.label for r in attribute_spans(
        spans, atoms, outcomes={atoms[0].id: "still_missing"}, categories={})}
    assert labels[accel.id] == "still_missing"  # not "redundant": nobody knew it
    labels = {r.span.id: r.label for r in attribute_spans(spans, atoms)}
    assert labels[accel.id] == "unscored"  # unprobed and unjudged: not "inert"
    labels = {r.span.id: r.label for r in attribute_spans(
        spans, atoms, categories={atoms[0].id: "novel_inert"})}
    assert labels[accel.id] == "inert"  # judged inert still reads as inert


def test_an_unread_span_is_labelled_unread_not_empty():
    spans, atoms, _ = _attributed()
    background = next(s for s in spans if "research prototype" in s.text)
    background.unread = True
    rows = attribute_spans(spans, atoms)
    row = next(r for r in rows if r.span.id == background.id)
    assert (row.label, row.colour) == ("unread", "hatched")
    assert all(r["span_id"] != background.id for r in dead_weight(rows))
    assert "hatched" in render_annotated_html(rows, title="t")
