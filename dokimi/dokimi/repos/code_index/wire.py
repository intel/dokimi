# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Protobuf wire-format reads for the SCIP decoder: varints, keys, length-delimited bodies.

Every read checks the bounds first, so a short buffer raises :class:`CodeIndexError` and never
``IndexError``. Groups (wire types 3 and 4) are deprecated and no SCIP tool writes them, so they are refused.
"""

from __future__ import annotations

from dokimi.repos.errors import CodeIndexError

VARINT = 0
FIXED64 = 1
LEN = 2
FIXED32 = 5


def read_varint(buf: memoryview, pos: int) -> tuple[int, int]:
    """The unsigned varint at ``pos`` and the position after it."""
    value = 0
    shift = 0
    for _ in range(10):
        if pos >= len(buf):
            raise CodeIndexError(f"truncated varint at byte {pos}")
        byte = buf[pos]
        pos += 1
        if shift == 63 and byte & 0x7F > 1:
            raise CodeIndexError(f"varint larger than 64 bits ending at byte {pos}")
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise CodeIndexError(f"varint longer than 10 bytes ending at byte {pos}")


def read_key(buf: memoryview, pos: int) -> tuple[int, int, int]:
    """The field number, the wire type and the position after the key at ``pos``."""
    key, pos = read_varint(buf, pos)
    field, wire = key >> 3, key & 7
    if field == 0:
        raise CodeIndexError(f"field number 0 at byte {pos}: not a protobuf message")
    return field, wire, pos


def read_delimited(buf: memoryview, pos: int) -> tuple[memoryview, int]:
    """The length-delimited body that starts at ``pos`` (a view, not a copy) and the position after it."""
    size, pos = read_varint(buf, pos)
    if size > len(buf) - pos:
        raise CodeIndexError(f"length {size} at byte {pos} runs past the end of the file: truncated?")
    return buf[pos:pos + size], pos + size


def skip(buf: memoryview, pos: int, wire: int) -> int:
    """The position after a field of wire type ``wire`` whose value starts at ``pos``."""
    if wire == VARINT:
        return read_varint(buf, pos)[1]
    if wire == LEN:
        return read_delimited(buf, pos)[1]
    width = {FIXED64: 8, FIXED32: 4}.get(wire)
    if width is None:
        raise CodeIndexError(f"unsupported wire type {wire} at byte {pos}")
    if width > len(buf) - pos:
        raise CodeIndexError(f"fixed{width * 8} field at byte {pos} runs past the end of the file: truncated?")
    return pos + width


def read_ints(buf: memoryview, pos: int, wire: int, out: list[int]) -> int:
    """Append one repeated integer field (packed, wire type 2, or one tag per value, wire type 0) to ``out``."""
    if wire == VARINT:
        value, pos = read_varint(buf, pos)
        out.append(value)
        return pos
    if wire == LEN:
        body, pos = read_delimited(buf, pos)
        at = 0
        while at < len(body):
            value, at = read_varint(body, at)
            out.append(value)
        return pos
    return skip(buf, pos, wire)
