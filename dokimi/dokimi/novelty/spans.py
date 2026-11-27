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
        r"(\bv?\d+\.\d+(\.\d+)?\b|\bsince\b|\bas of\b|\brequires?\b\s+(?:\S+\s*)?\d|"
        r"\bonly (?:on|in|with)\b|\bfrom\s+\d|\b(?:>=|<=|>|<)\s*\d)", re.I)),
    ("ordering", re.compile(
        r"\b(before|prior\s+to|after|first|then|finally|prerequisite|precede|follow(?:ed|s) by|"
        r"in (?:this|the following) order|must be (?:set|run|loaded) (?:before|after))\b", re.I)),
    # `%` is not a word character, so the unit alternation cannot end in `\b` — that
    # would make "80% utilization" undetectable, which is exactly a threshold.
    ("threshold", re.compile(
        r"\b\d+(\.\d+)?\s*(%|\b(?:x|GB|MB|KB|TB|GiB|MiB|ms|s|sec|seconds|cores?|threads?|"
        r"tokens?|bytes?|Hz|MHz|GHz)\b)", re.I)),
]

# The whole run of fence characters: a fence closes only on a run at least as long.
# A backtick run followed by another backtick on its line is inline code, not a fence.
_FENCE = re.compile(r"^\s*(`{3,}(?!.*`)|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# A Setext underline: `=` for a level 1 heading, `-` (two or more, a lone `-` is a list item) for level 2.
_SETEXT = re.compile(r"^ {0,3}(=+|-{2,})\s*$")
_INDENTED = re.compile(r"^(?: {4}|\t)")
_COMMENT_OPEN = re.compile(r"^ {0,3}<!--")
_QUOTE = re.compile(r"^ {0,3}>")
# A thematic break made of `*` or `_` (`---` is read as a Setext underline or a rule by `_SETEXT`).
_BREAK = re.compile(r"^ {0,3}([*_])(?:[ \t]*\1){2,}\s*$")
# a backtick run closed by a run of the same length, over soft line breaks but not over a blank line
_CODE = r"(?<!`)(`+)(?!`)(?:(?!\n[ \t\r]*\n)[\s\S])+?(?<!`)\1(?!`)"
# A closed comment (`<!-->` and `<!--->` too), or a code span or `\<`/`\\` escape matched first, so `\<!--` opens nothing.
_COMMENT = re.compile(rf"{_CODE}|\\[\\<]|<!--(?:-?>|.*?-->)", re.S)
_OPENER = re.compile(rf"{_CODE}|\\[\\<]|(<!--)", re.S)
# A sentence ends before a comment: the text so far ends in terminal punctuation and what follows starts a sentence.
_ENDS = re.compile(r"[.!?:]\s*$")
_STARTS = re.compile(r"\s*[A-Z`\-*\d]")


def _blank_comments(text: str, fill: str) -> str:
    """``text`` with each closed comment replaced by ``fill`` (repeated to its length when ``fill`` is one character)."""
    return _COMMENT.sub(
        lambda m: m.group() if not m.group().startswith("<!--") else (fill * len(m.group()) if len(fill) == 1 else fill), text)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# Sentence split on terminal punctuation followed by whitespace + a capital/backtick.
# Deliberately crude: over-splitting costs a little attribution granularity, while a
# heavyweight tokenizer would add a dependency for no measured gain.
_SENTENCE = re.compile(r"(?<=[.!?:])\s+(?=(?:\0+\s*)?[A-Z`\-*\d])")  # \0 is a masked comment: look past it


@dataclass
class Span:
    """One attributable region of the artifact's text.

    Attributes:
        id: Stable ``s<NNNN>`` id, assigned in document order.
        kind: ``heading`` | ``sentence`` | ``list_item`` | ``code`` | ``table_row`` | ``comment``.
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


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` closes a block opened by ``fence``: the same character, a run at
    least as long, and nothing after it."""
    run = line.strip()
    return run.startswith(fence) and not run.lstrip(fence[0])


def dangling_fence(text: str) -> str | None:
    """The marker of a code fence ``text`` opens and never closes, or ``None``.

    Same rule as :func:`segment`: a fence closes only on a line holding nothing but a run
    of the marker's character at least as long as the marker, so ```` ``` ```` inside a
    ``~~~`` block or a ```` ```` ```` block, or ```` ```python ```` in any block, is
    content. Anything that concatenates texts (the bundle renderer) must use this, not
    fence-line parity, or the two disagree and the next text is swallowed into one code span.
    """
    marker: str | None = None
    for line in text.splitlines():
        if marker is None:
            opened = _FENCE.match(line)
            marker = opened.group(1) if opened else None
        elif _closes(line, marker):
            marker = None
    return marker


def _frontmatter_end(lines: list[str]) -> int:
    """Index of the closing ``---`` of an opening frontmatter block, or -1: that line underlines nothing."""
    if not lines or lines[0].strip() != "---":
        return -1
    return next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), -1)


def _block_end(lines: list[str], i: int, keep) -> int:
    """Index after the last line from ``i`` that ``keep`` accepts, trailing blank lines excluded."""
    end = i
    j = i
    while j < len(lines) and (not lines[j].strip() or keep(lines[j])):
        j += 1
        if lines[j - 1].strip():
            end = j
    return end


def _enter_heading(path: list[str], level: int, chunk: str) -> None:
    """Make the heading in ``chunk`` the innermost heading of ``path`` at ``level``; level 0 leaves it.

    The title is the text after the ``#`` marks, or the lines above a Setext underline joined by a space.
    """
    if level:
        rows = [row.strip() for row in chunk.strip().splitlines()]
        setext = len(rows) > 1 and _SETEXT.match(rows[-1])
        del path[level - 1:]
        path.append(" ".join(rows[:-1]) if setext else _HEADING.sub(r"\2", rows[0]))


def _setext(lines: list[str], i: int) -> tuple[str, int, int] | None:
    """A Setext heading of one or more lines from ``i``: ``("heading", end line, level)``, or ``None``.

    A paragraph line that is a rule, list item, table row or blockquote opens no heading, and the closing ``---`` of an
    opening frontmatter block underlines nothing.
    """
    if (_SETEXT.match(lines[i]) or _BREAK.match(lines[i]) or _LIST_ITEM.match(lines[i])
            or _TABLE_ROW.match(lines[i]) or _QUOTE.match(lines[i])):
        return None
    j = i + 1
    while j < len(lines) and lines[j].strip() and not _SETEXT.match(lines[j]) and not (
            _HEADING.match(lines[j]) or _FENCE.match(lines[j]) or _LIST_ITEM.match(lines[j])
            or _TABLE_ROW.match(lines[j]) or _COMMENT_OPEN.match(lines[j]) or _QUOTE.match(lines[j])
            or _BREAK.match(lines[j])):
        j += 1
    if j >= len(lines) or not _SETEXT.match(lines[j]) or j == _frontmatter_end(lines):
        return None
    return "heading", j + 1, 1 if lines[j].lstrip().startswith("=") else 2


def _comment_end(lines: list[str], i: int) -> int:
    """Index after the line that closes the comment opened on line ``i``, or -1. Text after ``-->`` moves to its own line."""
    for n in range(i, len(lines)):
        body = lines[n].split("<!--", 1)[-1] if n == i else lines[n]
        short = n == i and body.startswith((">", "->"))  # `<!-->` and `<!--->` close at once
        if short or "-->" in body:
            cut = len(lines[n]) - len(body) + (body.index(">") + 1 if short else body.index("-->") + 3)
            cut += len(lines[n][cut:]) - len(lines[n][cut:].lstrip(" \t"))  # blanks stay with the comment
            if lines[n][cut:].strip():
                lines[n:n + 1] = [lines[n][:cut], lines[n][cut:]]
            return n + 1
    return -1


def _close_comment(lines: list[str], i: int, j: int) -> int:
    """``j``, or the index after the line closing the last comment ``lines[i:j]`` leaves open.

    A comment opened inside a paragraph or list item runs over blank and structural lines to its ``-->``;
    a later ``<!--`` on the line that closes it opens the next one, and the range grows until none is left open.
    """
    while any(m.group(2) for m in _OPENER.finditer(_blank_comments("".join(lines[i:j]), ""))):
        n = next((n for n in range(j, len(lines)) if "-->" in lines[n]), -1)
        if n < 0:
            break
        j = n + 1
    return j


def _mask(text: str) -> str:
    """``text`` with each closed comment blanked to ``\\0`` and the inside of each code span to ``_`` (same length).

    A sentence cut never falls in either; the first character of a code span stays, so a cut before it holds.
    A cut falls before a comment when the text after the comment starts a sentence.
    """
    return _COMMENT.sub(lambda m: "\0" * len(m.group()) if m.group().startswith("<!--")
                        else m.group()[0] + "_" * (len(m.group()) - 1), text)


def _list_item_end(lines: list[str], i: int, close: bool = True) -> int:
    """Index after the list item that starts on line ``i``: it continues over indented follow-on lines."""
    j = i + 1
    while j < len(lines) and lines[j].strip() and not _LIST_ITEM.match(lines[j]) \
            and not _HEADING.match(lines[j]) and not _FENCE.match(lines[j]) \
            and lines[j].startswith((" ", "\t")):
        j += 1
    return _close_comment(lines, i, j) if close else j


def _joined(items: list[tuple[str, str, int]], n: int) -> bool:
    """``items[n]`` and ``items[n + 2]`` are two parts of one sentence cut by the comment ``items[n + 1]``."""
    before, between, after = items[n:n + 3]
    return (between[0] == "comment" and after[0] != "comment"
            and not (_ENDS.search(before[1]) and _STARTS.match(after[1])))


def _emit_pieces(kind: str, chunk: str, start: int, hidden_from: int, emit) -> None:
    """Emit ``chunk`` as ``kind`` spans, each comment that runs past ``hidden_from`` as its own ``comment`` span.

    ``hidden_from`` is where the block ended before a comment carried it on. Such a comment is no part of the
    sentence or list item that opened it, so its text takes no part in measuring that rule. Prose is split into
    sentences, and a cut falls in no comment or code span.
    """
    pos = 0
    pieces: list[tuple[str, str, int]] = []
    for m in _COMMENT.finditer(chunk):
        if not m.group().startswith("<!--") or m.end() <= hidden_from:
            continue
        pieces += [(kind, chunk[pos:m.start()], pos), ("comment", m.group(), m.start())]
        pos = m.end()
    pieces.append((kind, chunk[pos:], pos))
    items: list[tuple[str, str, int]] = []  # (kind, text, offset) in document order
    for n, (piece_kind, piece, at) in enumerate(pieces):
        piece_kind = piece_kind if n == 0 or piece_kind == "comment" else "sentence"
        if piece_kind != "sentence":
            items.append((piece_kind, piece, at))
            continue
        cut = 0
        for boundary in [*_SENTENCE.finditer(_mask(piece)), None]:
            stop = boundary.start() if boundary else len(piece)
            items.append(("sentence", piece[cut:stop], at + cut))
            if boundary:
                cut = boundary.end()
    for n, (item_kind, text, at) in enumerate(items):
        # A sentence cut by comments is one rule: every part is read as the whole (``Never <!-- c --> ever <!-- d --> do``).
        lo = hi = n
        while lo >= 2 and _joined(items, lo - 2):
            lo -= 2
        while hi + 2 < len(items) and _joined(items, hi):
            hi += 2
        whole = " ".join(it[1] for it in items[lo:hi + 1] if it[0] != "comment") if item_kind != "comment" else text
        emit(item_kind, text, start + at, whole)


def _scaffold(lines: list[str], i: int, spans: list[Span], in_list: bool) -> tuple[str, int, int] | None:
    """The Markdown block that starts at line ``i`` and is not prose, as ``(kind, end line, heading level)``, or ``None``.

    A ``#`` heading, an indented code block (after a blank line, and not the continuation of a list item), an
    HTML comment that closes, or a Setext heading. A comment closing mid-line splits that line (in place) in two.
    ``in_list``: the line is inside a list item. A line directly under a list item continues it and underlines
    into no heading.
    """
    line = lines[i]
    atx = _HEADING.match(line)
    if atx:
        return "heading", i + 1, len(atx.group(1))
    if _INDENTED.match(line) and (i == 0 or not lines[i - 1].strip()) and not in_list:
        return "code", _block_end(lines, i, _INDENTED.match), 0
    if _COMMENT_OPEN.match(line):
        end = _comment_end(lines, i)
        return ("comment", end, 0) if end >= 0 else None
    if spans and spans[-1].kind == "list_item" and i > 0 and lines[i - 1].strip():
        return None
    return _setext(lines, i)


def segment(text: str) -> list[Span]:
    """Split artifact text into spans in document order.

    Fenced code blocks stay whole (splitting them would produce spans that are not
    independently meaningful), as do indented code blocks (four spaces or a tab after a
    blank line, outside a list) and HTML comments (``comment``, closed by ``-->``).
    Table rows and list items are one span each, a Setext heading (a line underlined by
    ``===`` or ``---``) is a ``heading``, and prose paragraphs are split into sentences.
    """
    spans: list[Span] = []
    heading_path: list[str] = []
    lines = text.splitlines(keepends=True)
    offset = 0
    i = 0
    in_list = False  # inside a list item: indented lines are its content until a line that is not indented
    after_comment = -1  # the line holding the text that followed a closing ``-->``: prose, whatever it starts with

    def emit(kind: str, chunk: str, start: int, read: str | None = None) -> None:
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
            # a comment, or one inside a sentence or list item, is not read as a rule
            protection=() if kind == "comment" else protection_tags(_blank_comments(read or stripped, " ")),
        ))

    while i < len(lines):
        line = lines[i]
        start = offset

        prose_only = i == after_comment and not _COMMENT_OPEN.match(line)

        if not prose_only and _FENCE.match(line):
            fence = _FENCE.match(line).group(1)  # type: ignore[union-attr]
            block = [line]
            offset += len(line)
            i += 1
            while i < len(lines):
                block.append(lines[i])
                offset += len(lines[i])
                closed = _closes(lines[i], fence)
                i += 1
                if closed:
                    break
            emit("code", "".join(block), start)
            continue

        if not line.strip() or _BREAK.match(line):  # a thematic break holds no text
            offset += len(line)
            i += 1
            continue

        n_lines = len(lines)
        in_list = in_list and line.startswith((" ", "\t"))
        scaffold = None if prose_only else _scaffold(lines, i, spans, in_list)
        if scaffold:
            kind, end, level = scaffold
            if len(lines) > n_lines:
                after_comment = end
            chunk = "".join(lines[i:end])
            _enter_heading(heading_path, level, chunk)
            emit(kind, chunk, start)
            offset += len(chunk)
            i = end
            continue

        if not prose_only and _TABLE_ROW.match(line):
            emit("table_row", line, start)
            offset += len(line)
            i += 1
            continue

        if not prose_only and _LIST_ITEM.match(line):
            # A list item continues over indented follow-on lines.
            in_list = True
            end = _list_item_end(lines, i)
            chunk = "".join(lines[i:end])
            _emit_pieces("list_item", chunk, start, len("".join(lines[i:_list_item_end(lines, i, False)])), emit)
            offset += len(chunk)
            i = end
            continue

        # Prose paragraph: consume to a blank line or a structural line, then sentence-split.
        block = [line]
        offset += len(line)
        i += 1
        while i < len(lines) and lines[i].strip() and not _HEADING.match(lines[i]) \
                and not _FENCE.match(lines[i]) and not _LIST_ITEM.match(lines[i]) \
                and not _TABLE_ROW.match(lines[i]) and not _COMMENT_OPEN.match(lines[i]) \
                and not _BREAK.match(lines[i]):
            block.append(lines[i])
            offset += len(lines[i])
            i += 1
        end = _close_comment(lines, i - len(block), i)
        hidden_from = len("".join(block))
        block.extend(lines[i:end])
        offset += sum(len(x) for x in lines[i:end])
        i = end
        paragraph = "".join(block)
        # Split on the paragraph with each closed comment blanked (same length), so no cut falls inside one.
        _emit_pieces("sentence", paragraph, start, hidden_from, emit)

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


def span_summary(spans: list[Span], atoms: list | None = None) -> dict:
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
    artifact. A sampling cap that is honest for one metric silently corrupts another. A
    capped run probes a sample but indexes every extracted atom, so this always writes
    ``false``; ``true`` is only on artifacts written before
    https://github.com/intel/dokimi/issues/371, and the gate still reads it there.

    Extractor recall is the other limit, and it is now measured rather than assumed
    (skill-novelty-evidence.md §10.7): three extractors over the same artifacts cited the same
    spans (Jaccard 0.87–0.99, each one's blind spot ≤2.4% of union tokens), so dead tokens are not
    mostly knowledge one model failed to formalize.

    Args:
        spans: Segmented artifact spans.
        atoms: Atoms to index onto the spans. Pass the full extracted set, a capped run's
            included (what :func:`~dokimi.novelty.atoms.extract_atoms` returns).
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
        "capped_atom_set": False,
        "sole_carrier": len(sole),
        "protected": sum(1 for s in spans if s.protected),
        "by_kind": {
            kind: sum(1 for s in spans if s.kind == kind)
            for kind in sorted({s.kind for s in spans})
        },
    }
