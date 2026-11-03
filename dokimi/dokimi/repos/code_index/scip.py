# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Decode a SCIP ``Index`` (scip.proto) into :class:`Occurrence` records and base-class relations.

Field numbers: Index.metadata=1, documents=2, external_symbols=3; Metadata.tool_info=2; ToolInfo.name=1,
version=2; Document.relative_path=1, occurrences=2, symbols=3; Occurrence.range=1, symbol=2, symbol_roles=3,
enclosing_range=7, single_line_range=8, multi_line_range=9, single_line_enclosing_range=10,
multi_line_enclosing_range=11 (the typed ranges win over range and enclosing_range when both are set);
SymbolInformation.symbol=1, relationships=4; Relationship.symbol=1, is_implementation=3.
Other field numbers are skipped, so an index written by a newer tool still reads. Lines and columns are
0-based, as in SCIP.
"""

from __future__ import annotations

from dataclasses import dataclass

from dokimi.repos.errors import CodeIndexError
from dokimi.repos.code_index.symbols import is_local
from dokimi.repos.code_index.wire import LEN, VARINT, read_delimited, read_ints, read_key, read_varint, skip

DEFINITION = 0x1
_INT32_MAX = 2**31 - 1

# (line, start_col, end_line, end_col, symbol, roles, enclosing span)
_Raw = tuple[int, int, int, int, str, int, tuple[int, int, int, int] | None]


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One occurrence of a symbol in one document.

    ``path`` is repo-relative (the index's relative path with the root prefix applied). The range starts at
    ``line``, ``start_col`` and ends at ``end_line``, ``end_col`` (``end_line == line`` for a one-line range).
    ``symbol`` is a global symbol string, or a ``local N`` id that is only meaningful within ``path``.
    ``enclosing`` is the first and last line of the enclosing range, or ``None`` when the tool wrote none."""

    path: str
    line: int
    start_col: int
    end_line: int
    end_col: int
    symbol: str
    roles: int
    enclosing: tuple[int, int] | None
    # the enclosing range's start and (exclusive) end as (line, col, line, col): nesting and emptiness need the columns
    enclosing_span: tuple[int, int, int, int] | None = None

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & DEFINITION)


@dataclass(frozen=True)
class Decoded:
    """What one index holds: the tool, the occurrences by repo-relative path, and the base symbols by symbol."""

    tool: str
    occurrences: dict[str, list[Occurrence]]
    bases: dict[str, tuple[str, ...]]


def decode_index(buf: memoryview, root_prefix: str = "") -> Decoded:
    """Decode a whole SCIP index. ``root_prefix`` (for example ``dokimi/``) is put before every document path."""
    if not len(buf):
        raise CodeIndexError("the file is empty, not a SCIP index")
    prefix = root_prefix if not root_prefix or root_prefix.endswith("/") else root_prefix + "/"
    tool_info: dict[int, str] = {}
    occurrences: dict[str, list[Occurrence]] = {}
    bases: dict[str, tuple[str, ...]] = {}
    intern: dict[bytes, str] = {}
    has_metadata = False
    pos = 0
    while pos < len(buf):
        field, wire, pos = read_key(buf, pos)
        if field == 1 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            _metadata(body, tool_info)
            has_metadata = True
        elif field == 2 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            path, raw = _document(body, intern, bases)
            full = prefix + path
            occurrences.setdefault(full, []).extend(
                Occurrence(full, line, start, end_line, end, symbol, roles, _lines(span), span)
                for line, start, end_line, end, symbol, roles, span in raw)
        elif field == 3 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            _symbol_information(body, intern, bases)
        else:
            pos = skip(buf, pos, wire)
    if not has_metadata:
        raise CodeIndexError("no metadata message: not a SCIP index, or truncated before its first field")
    # One path can come in several Document messages: keep each identical occurrence once, across them.
    occurrences = {path: list(dict.fromkeys(occs)) for path, occs in occurrences.items()}
    tool = f"{tool_info.get(1, '')} {tool_info.get(2, '')}".strip()
    return Decoded(tool, occurrences, bases)


def _text(raw: memoryview, what: str) -> str:
    try:
        return bytes(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CodeIndexError(f"{what} is not UTF-8") from exc


def _symbol(raw: memoryview, intern: dict[bytes, str]) -> str:
    """A symbol string, decoded once per distinct value: an index repeats each symbol in many occurrences."""
    key = bytes(raw)
    text = intern.get(key)
    if text is None:
        text = _text(raw, "symbol")
        intern[key] = text
    return text


def _metadata(body: memoryview, tool_info: dict[int, str]) -> None:
    """Merge the tool name (1) and version (2) of Metadata.tool_info into ``tool_info``. A repeated singular
    message merges in protobuf: a field the later copy sets replaces the earlier value, an absent one keeps it."""
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 2 and wire == LEN:
            info, pos = read_delimited(body, pos)
            at = 0
            while at < len(info):
                sub, sub_wire, at = read_key(info, at)
                if sub in (1, 2) and sub_wire == LEN:
                    raw, at = read_delimited(info, at)
                    tool_info[sub] = _text(raw, "tool name" if sub == 1 else "tool version")
                else:
                    at = skip(info, at, sub_wire)
        else:
            pos = skip(body, pos, wire)


def _document(body: memoryview, intern: dict[bytes, str],
              bases: dict[str, tuple[str, ...]]) -> tuple[str, list[_Raw]]:
    """The relative path and the raw occurrences of one Document (no root prefix yet). An occurrence repeated
    identically is kept once: scip-clang writes a header's occurrences again for each translation unit."""
    path = ""
    raw: list[_Raw] = []
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 1 and wire == LEN:
            text, pos = read_delimited(body, pos)
            path = _text(text, "relative_path")
        elif field == 2 and wire == LEN:
            occ, pos = read_delimited(body, pos)
            raw.append(_occurrence(occ, intern))
        elif field == 3 and wire == LEN:
            info, pos = read_delimited(body, pos)
            _symbol_information(info, intern, bases)
        else:
            pos = skip(body, pos, wire)
    if not path:
        raise CodeIndexError("a document has no relative_path")
    return path, list(dict.fromkeys(raw))


def _occurrence(body: memoryview, intern: dict[bytes, str]) -> _Raw:
    """(line, start_col, end_line, end_col, symbol, roles, enclosing span) of one Occurrence."""
    rng: list[int] = []
    enc: list[int] = []
    typed: dict[int, dict[int, int]] = {}
    symbol = ""
    roles = 0
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 1:
            pos = read_ints(body, pos, wire, rng)
        elif field == 2 and wire == LEN:
            raw, pos = read_delimited(body, pos)
            symbol = _symbol(raw, intern)
        elif field == 3 and wire == VARINT:
            roles, pos = read_varint(body, pos)
        elif field == 7:
            pos = read_ints(body, pos, wire, enc)
        elif field in (8, 9, 10, 11) and wire == LEN:
            raw, pos = read_delimited(body, pos)
            _typed_range(raw, field in (9, 11), typed.setdefault(field, {}))
        else:
            pos = skip(body, pos, wire)
    rng = _precedence(typed, (8, 9), rng)
    enc = _precedence(typed, (10, 11), enc)
    # [line, startCol, endCol] for a single-line range, [startLine, startCol, endLine, endCol] otherwise
    if len(rng) not in (3, 4):
        raise CodeIndexError(f"an occurrence range has {len(rng)} values, expected 3 or 4")
    _check_span(rng, "occurrence range")
    end_line = rng[2] if len(rng) == 4 else rng[0]
    return rng[0], rng[1], end_line, rng[-1], symbol, roles, _enclosing(enc)


def _precedence(typed: dict[int, dict[int, int]], fields: tuple[int, int], legacy: list[int]) -> list[int]:
    """The first typed range of ``fields`` that the occurrence sets, else the deprecated repeated-int range."""
    for field in fields:
        if field in typed:
            return _ordered(typed[field], field in (9, 11))
    return legacy


def _enclosing(enc: list[int]) -> tuple[int, int, int, int] | None:
    """The start and the exclusive end of an enclosing range as (line, col, line, col), or ``None`` when there is none."""
    if not enc:
        return None
    if len(enc) not in (3, 4):
        raise CodeIndexError(f"an enclosing range has {len(enc)} values, expected 3 or 4")
    _check_span(enc, "enclosing range")
    return (enc[0], enc[1], enc[0], enc[2]) if len(enc) == 3 else (enc[0], enc[1], enc[2], enc[3])


def _lines(span: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
    """The first and last line an enclosing span covers: a multiline range ending at column 0 leaves the line
    before its end uncovered, since its end is exclusive."""
    if span is None:
        return None
    first, _, end_line, end_col = span
    last = end_line - 1 if end_col == 0 and end_line > first else end_line
    return first, last


def _typed_range(body: memoryview, multi: bool, values: dict[int, int]) -> None:
    """Merge the values of a SingleLineRange (line, start column, end column) or a MultiLineRange (start line,
    start column, end line, end column) into ``values``. A repeated singular message merges as in protobuf, so
    a split message keeps the fields the first copy set."""
    wanted = (1, 2, 3, 4) if multi else (1, 2, 3)
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field in wanted and wire == VARINT:
            values[field], pos = read_varint(body, pos)
        else:
            pos = skip(body, pos, wire)


def _ordered(values: dict[int, int], multi: bool) -> list[int]:
    """The values in the order of the repeated-int range. An absent field is 0, as in protobuf."""
    return [values.get(field, 0) for field in ((1, 2, 3, 4) if multi else (1, 2, 3))]


def _check_span(values: list[int], what: str) -> None:
    """Refuse a span with a value outside int32 or an end before its start (the end is exclusive, so an empty
    span with equal start and end is valid)."""
    if max(values) > _INT32_MAX:
        raise CodeIndexError(f"{what} {values} has a value outside int32")
    start = (values[0], values[1])
    end = (values[0], values[2]) if len(values) == 3 else (values[2], values[3])
    if end < start:
        raise CodeIndexError(f"{what} {values} ends before it starts")


def _symbol_information(body: memoryview, intern: dict[bytes, str], bases: dict[str, tuple[str, ...]]) -> None:
    """Record the ``is_implementation`` targets of one SymbolInformation (a global symbol only; a local id is
    scoped to its document, so the same id in two documents would collide)."""
    symbol = ""
    implements: list[str] = []
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 1 and wire == LEN:
            raw, pos = read_delimited(body, pos)
            symbol = _symbol(raw, intern)
        elif field == 4 and wire == LEN:
            rel, pos = read_delimited(body, pos)
            target, is_implementation = _relationship(rel, intern)
            if is_implementation and target:
                implements.append(target)
        else:
            pos = skip(body, pos, wire)
    if symbol and implements and not is_local(symbol):
        bases[symbol] = tuple(dict.fromkeys(bases.get(symbol, ()) + tuple(implements)))


def _relationship(body: memoryview, intern: dict[bytes, str]) -> tuple[str, bool]:
    """(target symbol, is_implementation) of one Relationship."""
    target = ""
    is_implementation = False
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 1 and wire == LEN:
            raw, pos = read_delimited(body, pos)
            target = _symbol(raw, intern)
        elif field == 3 and wire == VARINT:
            value, pos = read_varint(body, pos)
            is_implementation = value != 0
        else:
            pos = skip(body, pos, wire)
    return target, is_implementation
