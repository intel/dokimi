# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The redirects of a shell command part that leave its stdout to the agent, and the quote masking they are matched
through, for the walker of :mod:`~dokimi.repos.shell_walk`."""

from __future__ import annotations

import re

from .shell_words import _stdout_after_stderr

# a redirect that leaves what a printing command shows on stdout: 2>&1, >&2, 2>/dev/null. A stdout discard
# (>/dev/null, 1>/dev/null, &>/dev/null) is not one of them: no content reaches the agent, so no read.
_HARMLESS_REDIRECT = re.compile(r"(?:(?<![^\s;&|(])\d+)?>&\d+(?=\s|$|[;&|])|(?<![^\s;&|(])(?!1(?![0-9]))\d+>>?\s*/dev/null(?=\s|$|[;&|])")
# stderr sent to a file: stdout, what a printing command shows, is unchanged
# (matched on the part with quoted text masked, so a quote glued to ``2`` or to the target is seen)
_STDERR_FILE = re.compile(r"(?<![^\s;&|(])2>>?[ \t]*[^\s;&|<>()]+")
# any redirect with its target: ``>f``, ``>>f``, ``2>f``, ``&>f``, ``>&2``, ``<f``
_REDIRECT = re.compile(r"(?<![^\s;&|(])(?:\d+|&)?(?:>>?|<)&?[ \t]*[^\s;&|<>()]+")


def bare(part: str) -> str:
    """``part`` with the text inside quotes emptied, so only shell syntax is left to match."""
    return re.sub(r"'[^']*'", "''", re.sub(r'"[^"]*"', '""', part))


def masked(part: str) -> str:
    """``part`` with the text inside quotes (and each backslash escape: ``\\"`` opens no quote) replaced by ``q``, same length."""
    return re.sub(r"\\.|'[^']*'|\"(?:\\.|[^\"\\])*\"",
                  lambda m: "qq" if m.group()[0] == "\\" else m.group()[0] + "q" * (len(m.group()) - 2) + m.group()[-1],
                  part, flags=re.DOTALL)


def without_stderr(part: str) -> str:
    """``part`` without the redirects that leave its stdout alone (``2>&1``, ``2>/dev/null``, ``2>file``)."""
    if not _stdout_after_stderr(bare(part)):  # else ``>&2`` stays: a redirect, so no read
        part = _outside_quotes(part, _HARMLESS_REDIRECT)
    return _outside_quotes(part, _STDERR_FILE)


def without_redirects(part: str) -> str:
    """``part`` without any redirect and its target (``>/dev/null``, ``2>f``, ``&>f``, ``>&2``, ``<f``), for the state a
    command sets, which a redirect does not change; ``part`` itself when it has none."""
    return _outside_quotes(part, _REDIRECT)


def _outside_quotes(part: str, rx: re.Pattern[str]) -> str:
    """``part`` with each match of ``rx`` removed, matched with the text inside quotes masked: a match
    starts outside quotes, and a quote glued to it (``'a'2>x``, ``2>x'y'``) is part of its word."""
    text = masked(part)
    out, last = [], 0
    for m in rx.finditer(text):
        out += [part[last:m.start()], " "]
        last = m.end()
    return "".join(out) + part[last:]
