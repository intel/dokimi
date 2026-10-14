# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Roll atom-level results back onto the artifact's text.

This is the layer that turns a verdict into an edit. Every span gets one label, chosen
worst-first — a span carrying one harmful atom and two useful ones is *red*, because the
author needs to see the harm, and averaging would bury it.

Two derived listings do most of the work in practice:

* **dead weight** — spans no atom came from, sorted by token cost. In the study eleven of
  thirteen skills sat at ≤0.17 of tokens; the two outliers (0.50, 0.74) were a judging-cap
  artifact and are 0.068 and 0.064 on the full atom set (evidence §10.7). A span no
  extraction call could read is ``unread``, not dead weight: nothing is known about it.
* **sole carriers** — spans that are the only source of some atom. These are what a
  careless edit silently deletes, so the shortener pins them.

`frac_carrying_nothing` is an upper bound on waste, not a measurement of it: some of those
tokens may be knowledge the extractor could not formalize. How many is measured across
extractors in skill-novelty-evidence.md §10.7.

A span citation is a claim, so :func:`carrier_confirmation` checks it. Measured on 257
atoms: the oracle recovers 0.833 of them from the cited span alone, 0.907 once the
heading path is prepended (~16 tokens — a span reading "Use 4 for most workloads" is not
a bad citation, it is a sentence whose subject lives in its heading), and **0.19 are not
recoverable from the cited file at all**. Those are syntheses inferred while reading the
whole document — the faithfulness verifier called 46 of 49 of them supported — so the
knowledge is real and the pointer is a fiction. They are reported as ``unlocated`` rather
than colouring a span that does not carry them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .atoms import Atom
from .spans import Span

# Label precedence, worst first. `colour` is the rendered signal; the labels are stable
# names the JSON artifact carries so a viewer can restyle without re-measuring.
LABELS = (
    "harmful", "degraded", "added", "unread", "protected", "redundant", "still_missing",
    "inert", "unscored", "empty",
)
COLOURS = {
    "harmful": "red",
    "degraded": "red",
    "added": "green",
    "unread": "hatched",
    "protected": "amber",
    "redundant": "grey",
    "still_missing": "blue",
    "inert": "grey",
    "unscored": "plain",
    "empty": "dim",
}
LEGEND = {
    "green": "carries knowledge the model did not have without this artifact",
    "grey": "carries only knowledge the model already produced unprompted, or judged inert",
    "red": "carries a conflicting atom, or one the artifact made the model get wrong",
    "amber": "protected: a negation, version gate, ordering constraint or threshold",
    "blue": "states something the model got wrong with and without it: rephrase or promote it",
    "plain": "carries atoms with no measured outcome and no judged category, so no colour is "
             "claimed",
    "hatched": "not read: every extraction call for this span failed, so what it carries "
               "is unknown",
    "dim": "no extracted atom — an upper bound on dead weight, not proof of it",
}


@dataclass
class SpanAttribution:
    """One span with its verdict, its cost, and the atoms behind both."""

    span: Span
    label: str
    atom_ids: list[str]
    outcomes: dict[str, int]
    categories: dict[str, int]
    sole_carrier_atoms: list[str]

    @property
    def colour(self) -> str:
        """Render colour for this span's label."""
        return COLOURS[self.label]

    def as_dict(self) -> dict:
        """Serializable form (what the views consume)."""
        return {
            "span_id": self.span.id,
            "kind": self.span.kind,
            "start": self.span.start,
            "end": self.span.end,
            "tokens": self.span.tokens,
            "heading_path": list(self.span.heading_path),
            "protection": list(self.span.protection),
            "label": self.label,
            "colour": self.colour,
            "atom_ids": list(self.atom_ids),
            "outcomes": dict(self.outcomes),
            "categories": dict(self.categories),
            "sole_carrier_atoms": list(self.sole_carrier_atoms),
        }


def attribute_spans(
    spans: list[Span],
    atoms: list[Atom],
    *,
    outcomes: dict[str, str] | None = None,
    categories: dict[str, str] | None = None,
) -> list[SpanAttribution]:
    """Attribute every span, in document order."""
    outcomes = outcomes or {}
    categories = categories or {}
    atoms_by_span: dict[str, list[Atom]] = {span.id: [] for span in spans}
    carrier_count: dict[str, int] = {}
    for atom in atoms:
        for span_id in atom.span_ids:
            if span_id in atoms_by_span:
                atoms_by_span[span_id].append(atom)
                carrier_count[atom.id] = carrier_count.get(atom.id, 0) + 1

    attributions: list[SpanAttribution] = []
    for span in spans:
        own = atoms_by_span.get(span.id, [])
        outcome_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        for atom in own:
            name = outcomes.get(atom.id)
            if name:
                outcome_counts[name] = outcome_counts.get(name, 0) + 1
            category = categories.get(atom.id)
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1
        attributions.append(SpanAttribution(
            span=span,
            label=_label(span, own, outcome_counts, category_counts),
            atom_ids=[a.id for a in own],
            outcomes=outcome_counts,
            categories=category_counts,
            sole_carrier_atoms=[a.id for a in own if carrier_count.get(a.id) == 1],
        ))
    return attributions


def _label(
    span: Span, atoms: list[Atom], outcomes: dict[str, int], categories: dict[str, int]
) -> str:
    """One label per span, worst first; measured outcomes before judged categories.

    No label is a fallback. An atom with neither a decisive outcome nor a category (one
    the value judge did not answer for, on a span that was not probed or stayed
    ``still_missing``) gives ``unscored`` or ``still_missing``, never ``redundant`` or
    ``inert``: those say "the model knew it" and "the task does not need it", and
    nobody measured or judged either.
    """
    if categories.get("harmful_conflicting"):
        return "harmful"
    if outcomes.get("degraded"):
        return "degraded"
    if outcomes.get("added"):
        return "added"
    if span.unread and not atoms:
        # Before protected: the extraction loss is the thing to fix, and the shortener
        # keeps both kinds of span anyway.
        return "unread"
    if span.protected:
        # Protected before redundant on purpose: a negation the model "already knew" in a
        # probe is still the line that stops a wrong default in a real run.
        return "protected"
    if not atoms:
        return "empty"
    if outcomes.get("redundant"):
        return "redundant"
    if outcomes.get("still_missing"):
        return "still_missing"
    if categories.get("novel_inert") or categories.get("known_inert") \
            or categories.get("scaffolding"):
        return "inert"
    return "unscored"


def dead_weight(attributions: list[SpanAttribution], *, limit: int = 20) -> list[dict]:
    """Spans no atom came from, most expensive first.

    Unread spans are not listed: they carry no atom because no call read them, which is
    an extraction loss (reported in ``extraction``), not dead weight.
    """
    empty = [a for a in attributions if a.label == "empty"]
    empty.sort(key=lambda a: a.span.tokens, reverse=True)
    return [
        {
            "span_id": a.span.id,
            "tokens": a.span.tokens,
            "kind": a.span.kind,
            "heading_path": list(a.span.heading_path),
            "text": a.span.text[:240],
        }
        for a in empty[:limit]
    ]


def sole_carriers(attributions: list[SpanAttribution], *, limit: int = 20) -> list[dict]:
    """Spans that are the only source of some atom — the ones an edit must not lose."""
    rows = [a for a in attributions if a.sole_carrier_atoms]
    rows.sort(key=lambda a: len(a.sole_carrier_atoms), reverse=True)
    return [
        {
            "span_id": a.span.id,
            "atoms": list(a.sole_carrier_atoms),
            "tokens": a.span.tokens,
            "protection": list(a.span.protection),
            "text": a.span.text[:240],
        }
        for a in rows[:limit]
    ]


def span_check_text(span: Span) -> str:
    """The text a span-level check should be run against: its heading path, then its body.

    Not cosmetic. With the bare span the oracle confirmed 0.833 of atoms against their own
    cited carrier; with the heading path prepended, 0.907, for about 16 tokens. Going wider
    than the heading made it *worse* (0.864 with both neighbours, 0.809 with the whole
    file), because the oracle retrieves before it entails and added text displaces the
    carrying chunk — so this is the measured stopping point, not a step on the way to
    supplying more.
    """
    path = " > ".join(span.heading_path)
    return f"{path}\n\n{span.text}" if path else span.text


def carrier_confirmation(
    spans: list[Span], atoms: list[Atom], *, oracle, limit_unlocated: int = 40
) -> dict:
    """Check each atom against the span(s) the extractor cited for it.

    An atom is *located* when the oracle recovers it from at least one cited span (checked
    with :func:`span_check_text`). One in five atoms in the study is not recoverable from
    its cited file at all, and those are not extraction defects — they are statements
    inferred from the document as a whole. A viewer must render them as unlocated instead
    of colouring a span for them, which is why this ships as a first-class count rather
    than as a warning.

    Costs one oracle verdict per (atom, cited span) pair. The oracle caches on disk and
    resolves most pairs lexically, so re-runs are cheap and the escalation count is
    visible in ``oracle.stats()``.
    """
    by_id = {span.id: span for span in spans}
    located: dict[str, str] = {}
    unlocated: list[str] = []
    checked = 0
    for atom in atoms:
        cited = [by_id[sid] for sid in atom.span_ids if sid in by_id]
        hit = None
        for span in cited:
            checked += 1
            if oracle.verdict(atom, span_check_text(span)).retained:
                hit = span.id
                break
        if hit is not None:
            located[atom.id] = hit
        else:
            unlocated.append(atom.id)
    total = len(atoms)
    return {
        "n_atoms": total,
        "n_pairs_checked": checked,
        "n_located": len(located),
        "frac_located": round(len(located) / total, 4) if total else 0.0,
        "unlocated_atoms": unlocated[:limit_unlocated],
        "n_unlocated": len(unlocated),
        "checked_with_heading_path": True,
        "note": (
            "an unlocated atom is not necessarily wrong: 0.19 of atoms in the study were "
            "unrecoverable from their cited file and the faithfulness check called 46 of "
            "49 of them supported — they read as syntheses of the whole document. Render "
            "them as unlocated; do not colour a span for them and do not delete them."
        ),
    }


def attribution_summary(
    attributions: list[SpanAttribution], *, confirmation: dict | None = None
) -> dict:
    """Token and span totals per label, plus the legend the views render.

    ``confirmation`` is :func:`carrier_confirmation`'s output when it was run; it travels
    with the labels because a colour on an unconfirmed span is a claim the run cannot make.
    """
    labels = {label: 0 for label in LABELS}
    tokens = {label: 0 for label in LABELS}
    for row in attributions:
        labels[row.label] += 1
        tokens[row.label] += row.span.tokens
    total_tokens = sum(tokens.values())
    out = {
        "spans_by_label": labels,
        "tokens_by_label": tokens,
        "token_share_by_label": {
            label: round(value / total_tokens, 4) if total_tokens else 0.0
            for label, value in tokens.items()
        },
        "legend": dict(LEGEND),
    }
    if confirmation is not None:
        out["carrier_confirmation"] = confirmation
    return out


def topic_rollup(attributions: list[SpanAttribution], *, depth: int = 2) -> list[dict]:
    """Aggregate spans by heading path — the coarse end of the attribution axis.

    Section-level rollup is available now because headings are an authoring structure
    that already exists. It reports label counts, not one label, because no topic in
    skill-novelty-evidence.md §10.4 was unanimous enough to carry one. Clustering atoms into
    *derived* topics would need a granularity of its own: too coarse says "the whole file is
    redundant", too fine repeats the atom table.
    """
    groups: dict[tuple[str, ...], list[SpanAttribution]] = {}
    for row in attributions:
        key = tuple(row.span.heading_path[:depth])
        groups.setdefault(key, []).append(row)
    out = []
    for key, rows in groups.items():
        tokens = sum(r.span.tokens for r in rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.label] = counts.get(row.label, 0) + 1
        out.append({
            "heading_path": list(key),
            "spans": len(rows),
            "tokens": tokens,
            "labels": counts,
            "added_spans": counts.get("added", 0),
            "dead_tokens": sum(r.span.tokens for r in rows if r.label == "empty"),
            "unread_tokens": sum(r.span.tokens for r in rows if r.label == "unread"),
        })
    out.sort(key=lambda row: row["tokens"], reverse=True)
    return out
