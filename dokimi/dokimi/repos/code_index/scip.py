# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Decode a SCIP ``Index`` (scip.proto) into :class:`Occurrence` records and base-class relations.

Field numbers: Index.metadata=1, documents=2, external_symbols=3; Metadata.tool_info=2; ToolInfo.name=1,
version=2; Document.relative_path=1, occurrences=2, symbols=3; Occurrence.range=1, symbol=2, symbol_roles=3,
enclosing_range=7; SymbolInformation.symbol=1, relationships=4; Relationship.symbol=1, is_implementation=3.
Other field numbers are skipped, so an index written by a newer tool still reads. Lines and columns are
0-based, as in SCIP.
"""

from __future__ import annotations

from dataclasses import dataclass

from dokimi.repos.errors import CodeIndexError
from dokimi.repos.code_index.symbols import is_local
from dokimi.repos.code_index.wire import LEN, VARINT, read_delimited, read_ints, read_key, read_varint, skip

DEFINITION = 0x1


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One occurrence of a symbol in one document.

    ``path`` is repo-relative (the index's relative path with the root prefix applied). ``symbol`` is a global
    symbol string, or a ``local N`` id that is only meaningful within ``path``. ``enclosing`` is the first and
    last line of the enclosing range, or ``None`` when the tool wrote none."""

    path: str
    line: int
    start_col: int
    end_col: int
    symbol: str
    roles: int
    enclosing: tuple[int, int] | None

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
    tool = ""
    occurrences: dict[str, list[Occurrence]] = {}
    bases: dict[str, tuple[str, ...]] = {}
    intern: dict[bytes, str] = {}
    has_metadata = False
    pos = 0
    while pos < len(buf):
        field, wire, pos = read_key(buf, pos)
        if field == 1 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            tool = _metadata_tool(body)
            has_metadata = True
        elif field == 2 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            path, raw = _document(body, intern, bases)
            full = prefix + path
            occurrences.setdefault(full, []).extend(
                Occurrence(full, line, start, end, symbol, roles, enclosing)
                for line, start, end, symbol, roles, enclosing in raw)
        elif field == 3 and wire == LEN:
            body, pos = read_delimited(buf, pos)
            _symbol_information(body, intern, bases)
        else:
            pos = skip(buf, pos, wire)
    if not has_metadata:
        raise CodeIndexError("no metadata message: not a SCIP index, or truncated before its first field")
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


def _metadata_tool(body: memoryview) -> str:
    """``name version`` from Metadata.tool_info; the other metadata fields are not used."""
    tool = ""
    pos = 0
    while pos < len(body):
        field, wire, pos = read_key(body, pos)
        if field == 2 and wire == LEN:
            info, pos = read_delimited(body, pos)
            name = version = ""
            at = 0
            while at < len(info):
                sub, sub_wire, at = read_key(info, at)
                if sub in (1, 2) and sub_wire == LEN:
                    raw, at = read_delimited(info, at)
                    if sub == 1:
                        name = _text(raw, "tool name")
                    else:
                        version = _text(raw, "tool version")
                else:
                    at = skip(info, at, sub_wire)
            tool = f"{name} {version}".strip()
        else:
            pos = skip(body, pos, wire)
    return tool


def _document(body: memoryview, intern: dict[bytes, str],
              bases: dict[str, tuple[str, ...]]) -> tuple[str, list[tuple[int, int, int, str, int, tuple[int, int] | None]]]:
    """The relative path and the raw occurrences of one Document (no root prefix yet)."""
    path = ""
    raw: list[tuple[int, int, int, str, int, tuple[int, int] | None]] = []
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
    return path, raw


def _occurrence(body: memoryview, intern: dict[bytes, str]) -> tuple[int, int, int, str, int, tuple[int, int] | None]:
    """(line, start_col, end_col, symbol, roles, enclosing lines) of one Occurrence."""
    rng: list[int] = []
    enc: list[int] = []
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
        else:
            pos = skip(body, pos, wire)
    # [line, startCol, endCol] for a single-line range, [startLine, startCol, endLine, endCol] otherwise
    if len(rng) not in (3, 4):
        raise CodeIndexError(f"an occurrence range has {len(rng)} values, expected 3 or 4")
    if enc and len(enc) not in (3, 4):
        raise CodeIndexError(f"an enclosing range has {len(enc)} values, expected 3 or 4")
    enclosing = (enc[0], enc[2] if len(enc) == 4 else enc[0]) if enc else None
    return rng[0], rng[1], rng[-1], symbol, roles, enclosing


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
