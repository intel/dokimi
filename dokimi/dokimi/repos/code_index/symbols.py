# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""SCIP symbol strings: ``local N`` ids, and the name a global symbol defines.

A global symbol is ``<scheme> <manager> <package> <version> <descriptors>``, with the first four fields
separated by single spaces (a space inside a field is written twice). The descriptors are the grammar of
scip.proto: ``name/`` namespace, ``name#`` type, ``name.`` term, ``name:`` meta, ``name!`` macro,
``name(disambiguator).`` method, ``[param]`` type parameter, ``(param)`` parameter. Names may be backtick-escaped.
"""

from __future__ import annotations

_SIMPLE_EXTRA = "_+-$"
_DEFINING_SUFFIXES = "/#.:!"


def is_local(symbol: str) -> bool:
    """True for a ``local N`` id, which is scoped to one document."""
    return symbol.startswith("local ")


def descriptor_name(symbol: str) -> str | None:
    """The name of the symbol's last descriptor: ``Impl#run().`` gives ``run``, ``Impl#run().(value)`` gives
    ``value`` (a parameter defines its own name). A local id, an empty symbol, or one that does not follow
    the grammar gives ``None``."""
    if is_local(symbol) or not symbol:
        return None
    start = _descriptors_start(symbol)
    if start is None:
        return None
    desc = symbol[start:]
    last: str | None = None
    pos = 0
    while pos < len(desc):
        read = _read_name(desc, pos)
        if read is None or read[1] >= len(desc):
            return None
        name, pos = read
        mark = desc[pos]
        if name and mark in _DEFINING_SUFFIXES:
            last = name
            pos += 1
        elif name and mark == "(":
            end = _method_end(desc, pos)
            if end is None:
                return None
            last = name
            pos = end
        elif not name and mark in "([":
            # a parameter `(name)` or a type parameter `[name]`: the name is inside the brackets
            inner = _read_name(desc, pos + 1)
            closing = ")" if mark == "(" else "]"
            if inner is None or not inner[0] or inner[1] >= len(desc) or desc[inner[1]] != closing:
                return None
            last = inner[0]
            pos = inner[1] + 1
        else:
            return None
    return last


def _method_end(desc: str, pos: int) -> int | None:
    """The position after ``(disambiguator).`` of a method whose ``(`` is at ``pos``. The disambiguator is an
    escaped or simple name, or empty, so a ``)`` inside backticks does not close it."""
    inner = _read_name(desc, pos + 1)
    if inner is None or inner[1] >= len(desc) or desc[inner[1]] != ")":
        return None
    close = inner[1]
    if desc[close + 1:close + 2] != ".":
        return None
    return close + 2


def _descriptors_start(symbol: str) -> int | None:
    """The offset of the descriptors: the position after the fourth single-space separator."""
    at = 0
    for _ in range(4):
        while True:
            sep = symbol.find(" ", at)
            if sep < 0:
                return None
            at = sep + 1
            # two spaces inside a field are one escaped space, not a separator
            if at < len(symbol) and symbol[at] == " ":
                at += 1
                continue
            break
    return at


def _read_name(desc: str, pos: int) -> tuple[str, int] | None:
    """The name at ``pos``: a backtick-escaped name (`` `` `` is a backtick) or a simple identifier, which may be
    empty before ``(`` or ``[``."""
    if pos < len(desc) and desc[pos] == "`":
        chars: list[str] = []
        at = pos + 1
        while True:
            tick = desc.find("`", at)
            if tick < 0:
                return None
            chars.append(desc[at:tick])
            if tick + 1 < len(desc) and desc[tick + 1] == "`":
                chars.append("`")
                at = tick + 2
                continue
            return "".join(chars), tick + 1
    end = pos
    while end < len(desc) and (desc[end].isascii() and (desc[end].isalnum() or desc[end] in _SIMPLE_EXTRA)):
        end += 1
    return desc[pos:end], end
