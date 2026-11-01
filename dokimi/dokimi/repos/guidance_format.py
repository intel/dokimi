# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Guidance rendered in another format: the ``guidance:format=<fmt>`` arm (contract §3, step 5).

``xml`` wraps a guidance file in a ``<guidance>`` element and turns each ATX heading into a
``<section level=".." title="..">`` element, closed before the next heading of its level or
above. No other line changes, and :func:`restore` undoes the wrapping exactly. :func:`to_format`
refuses a file whose rendering does not restore to it (a body line that looks like one of the
tags, a heading the restore could not rebuild), so the arm never measures altered content.
Headings inside code fences are body text.
"""

from __future__ import annotations

import re

#: The formats of ``guidance:format=<fmt>``. Markdown is ``guidance:raw``, not a format of its own.
FORMATS = ("xml",)

_ROOT, _ROOT_CLOSE, _CLOSE = "<guidance>", "</guidance>", "</section>"
_HEADING = re.compile(r"^(#{1,6}) (\S[^\r]*)$")  # canonical only: one space, no CR; anything else is body text
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_OPEN = re.compile(r'^<section level="([1-6])" title="([^"]*)">$')
_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
_UNESCAPES = {v: k for k, v in _ESCAPES.items()}


class FormatError(ValueError):
    """A guidance file that cannot be rendered in the format, or a rendering that would not restore."""


def to_format(text: str, fmt: str) -> str:
    """``text`` (Markdown) rendered as ``fmt``; raises :class:`FormatError` unless :func:`restore` gives ``text``."""
    if fmt not in FORMATS:
        raise FormatError(f"unknown guidance format {fmt!r} (known: {', '.join(FORMATS)})")
    rendered = _render_xml(text)
    if restore(rendered) != text:
        raise FormatError(f"the {fmt} rendering would not restore to the file (a line of it looks like a tag)")
    return rendered


def restore(rendered: str) -> str:
    """The Markdown a rendering came from: the wrapper lines removed, each section tag back to its heading."""
    lines = rendered.split("\n")
    if len(lines) < 2 or lines[0] != _ROOT or lines[-1] != _ROOT_CLOSE:
        raise FormatError("not a guidance rendering: the root element is missing")
    out = []
    for line in lines[1:-1]:
        if line == _CLOSE:
            continue
        match = _OPEN.match(line)
        out.append(f"{'#' * int(match.group(1))} {_unescape(match.group(2))}" if match else line)
    return "\n".join(out)


def _render_xml(text: str) -> str:
    out, fenced = [_ROOT], False
    open_levels: list[int] = []
    for line in text.split("\n"):
        heading = None if fenced else _HEADING.match(line)
        if _FENCE.match(line):
            fenced = not fenced
        if heading is None:
            out.append(line)
            continue
        level = len(heading.group(1))
        while open_levels and open_levels[-1] >= level:
            out.append(_CLOSE)
            open_levels.pop()
        out.append(f'<section level="{level}" title="{_escape(heading.group(2))}">')
        open_levels.append(level)
    out.extend([_CLOSE] * len(open_levels))
    out.append(_ROOT_CLOSE)
    return "\n".join(out)


def _escape(title: str) -> str:
    return re.sub(r'[&<>"]', lambda m: _ESCAPES[m.group()], title)


def _unescape(title: str) -> str:
    return re.sub(r"&(?:amp|lt|gt|quot);", lambda m: _UNESCAPES[m.group()], title)
