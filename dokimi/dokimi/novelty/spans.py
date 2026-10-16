# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Segment an artifact into spans, and flag the spans that must not be dropped.

A span is the unit of *attribution*: small enough that colouring it tells an author
which sentence to edit, large enough to carry a checkable statement. Knowledge atoms
point back at span ids, so every number the novelty layer reports can be rendered on
top of the artifact's own text.

Protection flags are deliberately computed here, lexically, with no model involved.
They mark the spans whose value is *conditional on a failure* — negations, version
gates, ordering constraints, thresholds — which a value-ranking selector discards
precisely because nothing in their statement encodes the cost of being wrong. A
measured shortening kept 0.916 of the knowledge at 21% of the tokens and only 0.158
of these spans; the ``protection`` axis exists because of that run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered: the first matching rule names the tag, but a span can carry several.
_PROTECTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("negation", re.compile(
        r"\b(do not|don't|never|must not|cannot|can't|avoid|no longer|"
        r"unsupported|not supported|deprecated|instead of|rather than)\b", re.I)),
    ("version_gate", re.compile(
        r"(\bv?\d+\.\d+(\.\d+)?\b|\bsince\b|\bas of\b|\brequires?\b\s+\S*\s*\d|"
        r"\bonly (?:on|in|with)\b|\bfrom\s+\d|\b(?:>=|<=|>|<)\s*\d)", re.I)),
    ("ordering", re.compile(
        r"\b(before|after|first|then|finally|prerequisite|precede|follow(?:ed|s) by|"
        r"in (?:this|the following) order|must be (?:set|run|loaded) (?:before|after))\b", re.I)),
    # `%` is not a word character, so the unit alternation cannot end in `\b` — that
    # would make "80% utilization" undetectable, which is exactly a threshold.
    ("threshold", re.compile(
        r"\b\d+(\.\d+)?\s*(%|\b(?:x|GB|MB|KB|TB|GiB|MiB|ms|s|sec|seconds|cores?|threads?|"
        r"tokens?|bytes?|Hz|MHz|GHz)\b)", re.I)),
]

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# Sentence split on terminal punctuation followed by whitespace + a capital/backtick.
# Deliberately crude: over-splitting costs a little attribution granularity, while a
# heavyweight tokenizer would add a dependency for no measured gain.
_SENTENCE = re.compile(r"(?<=[.!?:])\s+(?=[A-Z`\-*\d])")


@dataclass
class Span:
    """One attributable region of the artifact's text.

    Attributes:
        id: Stable ``s<NNNN>`` id, assigned in document order.
        kind: ``heading`` | ``sentence`` | ``list_item`` | ``code`` | ``table_row``.
        text: The span's own text, verbatim.
        start: Character offset of ``text`` in the artifact.
        end: Character offset one past the span.
        heading_path: Enclosing headings, outermost first — the "where am I" trail.
        protection: Protection tags (see :data:`_PROTECTION_RULES`); empty for most spans.
        atom_ids: Atoms extracted from this span (see :func:`index_atoms`).
        unread: No extraction call could read this span (see
            :class:`~dokimi.novelty.atoms.ExtractionReport`). Carrying no atom then says
            nothing about the text, so it is not counted as carrying nothing.
    """

    id: str
    kind: str
    text: str
    start: int
    end: int
    heading_path: tuple[str, ...] = ()
    protection: tuple[str, ...] = ()
    atom_ids: list[str] = field(default_factory=list)
    unread: bool = False

    @property
    def tokens(self) -> int:
        """Rough token count (chars / 4).

        An estimate on purpose: span counts are compared against each other inside
        one artifact, and a tokenizer per provider would make the same span report
        different sizes per model without changing any decision.
        """
        return max(1, round(len(self.text) / 4))

    @property
    def protected(self) -> bool:
        """True when dropping this span can change an outcome, not just a phrasing."""
        return bool(self.protection)

    @property
    def unknown(self) -> bool:
        """Unread and carrying no atom: what this span asserts is not known."""
        return self.unread and not self.atom_ids

    def as_dict(self) -> dict:
        """Serializable form (used by the ``novelty.v1`` artifact and the views)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "heading_path": list(self.heading_path),
            "protection": list(self.protection),
            "tokens": self.tokens,
            "atom_ids": list(self.atom_ids),
        }


def protection_tags(text: str) -> tuple[str, ...]:
    """Protection tags for one piece of text, in rule order."""
    return tuple(tag for tag, rule in _PROTECTION_RULES if rule.search(text))


def dangling_fence(text: str) -> str | None:
    """The marker of a code fence ``text`` opens and never closes, or ``None``.

    Same rule as :func:`segment`: a fence closes only on a line starting with the marker
    that opened it, so ```` ``` ```` inside a ``~~~`` block is content. Anything that
    concatenates texts (the bundle renderer) must use this, not fence-line parity, or the
    two disagree and the next text is swallowed into one code span.
    """
    marker: str | None = None
    for line in text.splitlines():
        if marker is None:
            opened = _FENCE.match(line)
            marker = opened.group(1) if opened else None
        elif line.strip().startswith(marker):
            marker = None
    return marker


def segment(text: str) -> list[Span]:
    """Split artifact text into spans in document order.

    Fenced code blocks stay whole (splitting them would produce spans that are not
    independently meaningful), table rows and list items are one span each, and prose
    paragraphs are split into sentences.
    """
    spans: list[Span] = []
    heading_path: list[str] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    i = 0

    def emit(kind: str, chunk: str, start: int) -> None:
        stripped = chunk.strip()
        if not stripped:
            return
        # Anchor the trimmed text inside the raw chunk so offsets stay exact.
        lead = len(chunk) - len(chunk.lstrip())
        begin = start + lead
        spans.append(Span(
            id=f"s{len(spans) + 1:04d}",
            kind=kind,
            text=stripped,
            start=begin,
            end=begin + len(stripped),
            heading_path=tuple(heading_path),
            protection=protection_tags(stripped),
        ))

    while i < len(lines):
        line = lines[i]
        start = offset

        if _FENCE.match(line):
            fence = _FENCE.match(line).group(1)  # type: ignore[union-attr]
            block = [line]
            offset += len(line)
            i += 1
            while i < len(lines):
                block.append(lines[i])
                offset += len(lines[i])
                closed = lines[i].strip().startswith(fence)
                i += 1
                if closed:
                    break
            emit("code", "".join(block), start)
            continue

        heading = _HEADING.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            del heading_path[level - 1:]
            heading_path.append(title)
            emit("heading", line, start)
            offset += len(line)
            i += 1
            continue

        if _TABLE_ROW.match(line):
            emit("table_row", line, start)
            offset += len(line)
            i += 1
            continue

        if _LIST_ITEM.match(line):
            # A list item continues over indented follow-on lines.
            block = [line]
            offset += len(line)
            i += 1
            while i < len(lines) and lines[i].strip() and not _LIST_ITEM.match(lines[i]) \
                    and not _HEADING.match(lines[i]) and not _FENCE.match(lines[i]) \
                    and lines[i].startswith((" ", "\t")):
                block.append(lines[i])
                offset += len(lines[i])
                i += 1
            emit("list_item", "".join(block), start)
            continue

        if not line.strip():
            offset += len(line)
            i += 1
            continue

        # Prose paragraph: consume to a blank line or a structural line, then sentence-split.
        block = [line]
        offset += len(line)
        i += 1
        while i < len(lines) and lines[i].strip() and not _HEADING.match(lines[i]) \
                and not _FENCE.match(lines[i]) and not _LIST_ITEM.match(lines[i]) \
                and not _TABLE_ROW.match(lines[i]):
            block.append(lines[i])
            offset += len(lines[i])
            i += 1
        paragraph = "".join(block)
        cursor = start
        for sentence in _SENTENCE.split(paragraph):
            if not sentence:
                continue
            found = text.find(sentence.strip(), cursor) if sentence.strip() else -1
            begin = found if found >= 0 else cursor
            emit("sentence", sentence, begin)
            cursor = begin + len(sentence)

    return spans


def index_atoms(spans: list[Span], atoms: list) -> None:
    """Fill each span's ``atom_ids`` from the atoms' ``span_ids`` (in place)."""
    by_id = {span.id: span for span in spans}
    for span in spans:
        span.atom_ids = []
    for atom in atoms:
        for span_id in getattr(atom, "span_ids", ()) or ():
            target = by_id.get(span_id)
            if target is not None and atom.id not in target.atom_ids:
                target.atom_ids.append(atom.id)


def span_summary(spans: list[Span], atoms: list | None = None, *, atom_cap: int | None = None) -> dict:
    """Aggregate span statistics for the ``novelty.v1`` ``spans{}`` block.

    ``tokens_carrying_nothing`` counts tokens in spans from which the extractor drew no
    atom, so it is an upper bound on waste rather than a measurement of it. Spans no
    extraction call could read (``unread``) are left out of it and of the denominator,
    and reported as ``n_unread`` / ``tokens_unread``: a span nobody read carries no atom
    for a reason that says nothing about the text, and counting it inflated the fraction
    one-for-one with the extraction loss.

    **It must be derived from the full atom set.** Computing it from a capped or
    otherwise sampled subset does not approximate the full-set figure, it inverts it: in
    the study a 250-atom-per-artifact judging cap turned a real 0.064 into an apparent
    0.743 on the largest artifact, and the two artifacts labelled "bulk without knowledge"
    were simply the two largest. Where the cap did not bind, the two agreed exactly. So
    ``n_atoms_indexed`` and ``capped_atom_set`` ship next to the fraction, and a reader
    who sees ``capped_atom_set: true`` must not read the fraction as a property of the
    artifact. A sampling cap that is honest for one metric silently corrupts another.

    Extractor recall is the other limit, and it is now measured rather than assumed
    (skill-novelty-evidence.md §10.7): three extractors over the same artifacts cited the same
    spans (Jaccard 0.87–0.99, each one's blind spot ≤2.4% of union tokens), so dead tokens are not
    mostly knowledge one model failed to formalize.

    Args:
        spans: Segmented artifact spans.
        atoms: Atoms to index onto the spans. Pass the full extracted set.
        atom_cap: The cap the extraction ran under, if any, so a capped set is flagged
            rather than silently averaged.
    """
    if atoms is not None:
        index_atoms(spans, atoms)
    carrier_count: dict[str, int] = {}
    for span in spans:
        for atom_id in span.atom_ids:
            carrier_count[atom_id] = carrier_count.get(atom_id, 0) + 1
    sole = [s.id for s in spans if any(carrier_count.get(a) == 1 for a in s.atom_ids)]
    unknown = [s for s in spans if s.unknown]
    empty = [s for s in spans if not s.atom_ids and not s.unknown]
    total_tokens = sum(s.tokens for s in spans)
    unknown_tokens = sum(s.tokens for s in unknown)
    read_tokens = total_tokens - unknown_tokens
    empty_tokens = sum(s.tokens for s in empty)
    return {
        "n": len(spans),
        "tokens": total_tokens,
        "tokens_carrying_nothing": empty_tokens,
        "frac_carrying_nothing": round(empty_tokens / read_tokens, 4) if read_tokens else 0.0,
        "n_unread": len(unknown),
        "tokens_unread": unknown_tokens,
        "n_atoms_indexed": len(atoms) if atoms is not None else None,
        "capped_atom_set": bool(atom_cap and atoms is not None and len(atoms) >= atom_cap),
        "sole_carrier": len(sole),
        "protected": sum(1 for s in spans if s.protected),
        "by_kind": {
            kind: sum(1 for s in spans if s.kind == kind)
            for kind in sorted({s.kind for s in spans})
        },
    }
