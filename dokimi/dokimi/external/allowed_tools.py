# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How ``allowed-tools`` is read: its entries (:func:`_tool_entries`), and ``allowed-tools-unrestricted-bash``.

``Bash`` with no pattern, or with a pattern that matches every command (``Bash(*)``), is a ``warn``
in :mod:`dokimi.external.validate`. Its message names a ``pipe-to-shell`` or ``permission-bypass-flag``
hit in the same skill (:mod:`dokimi.external.command_checks`), since those commands then run with no
prompt. In the GitSkills corpus 45.8% of the skills that declare ``allowed-tools`` grant Bash this way,
and those skills carry ``curl | sh`` and permission-bypass flags about twice as often as the others.
"""

from __future__ import annotations

from typing import Optional

# A ``Bash(...)`` pattern that matches every command: the same grant as a bare ``Bash``.
UNRESTRICTED_BASH_PATTERNS = frozenset({"", "*", ":*", "**", "* *", "*:*"})
# The command checks whose hit makes an unrestricted ``Bash`` grant riskier.
RISKY_COMMAND_CODES = ("pipe-to-shell", "permission-bypass-flag")


def is_unrestricted(pattern: Optional[str]) -> bool:
    """Whether a ``Bash`` entry with this ``(pattern)`` (None: no pattern) allows every command."""
    return pattern is None or pattern.strip() in UNRESTRICTED_BASH_PATTERNS


def _group_end(value: str, start: int, quotes: bool = True) -> Optional[int]:
    """The index just past the ``)`` that closes the ``(`` at ``start``, or None when the group never closes.

    Nested parentheses count. With ``quotes``, quoted text inside the group is opaque, so a ``)`` in quotes does
    not close it; a quote that never closes makes the group fail, and the caller reads that quote as a plain
    character (``Bash(echo don't)``) with ``quotes=False``.
    """
    depth, quote = 0, ""
    for i in range(start, len(value)):
        c = value[i]
        if quote:
            if c == quote:
                quote = ""
        elif quotes and c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _group_end_checked(value: str, start: int, quotes: bool) -> tuple[Optional[int], bool]:
    """``(_group_end(...), quotes still on)``. A quote that never closes switches quotes off for the rest of
    the value: that is one failed quoted scan at most, so the whole value is read in linear time."""
    if quotes:
        end = _group_end(value, start)
        if end is not None:
            return end, True
    return _group_end(value, start, quotes=False), False


def tool_names(value: str) -> list[str]:
    """The tool name of each entry of one ``allowed-tools`` string (see :func:`_tool_entries`)."""
    return [name for name, _ in _tool_entries(value)]


def _tool_entries(value: str) -> list[tuple[str, Optional[str]]]:
    """The tool name and ``(pattern)`` text (None when there is none) of each entry of one ``allowed-tools`` string, in one pass over it.

    Entries are split on whitespace and commas. A ``(pattern)`` after a name is consumed whole (see
    :func:`_group_end`). A group that never closes runs to the end of the string: its tool name is kept,
    its pattern is the text from its ``(`` on (never unrestricted), and the scan stops. Quoting is a heuristic, and a stray quote can hide the entries between two quotes or
    leave a junk name such as ``")"`` (warn-only, and rare).
    """
    names: list[tuple[str, Optional[str]]] = []
    i, n = 0, len(value)
    quotes = True
    while i < n:
        c = value[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c == "(":  # a pattern with no tool name: skipped
            end, quotes = _group_end_checked(value, i, quotes)
            if end is None:
                break
            i = end
            continue
        j = i
        while j < n and not (value[j].isspace() or value[j] in ",("):
            j += 1
        name = value[i:j]
        i = j
        k = i  # `Bash (git:*)`: spaces between the name and its group do not end the entry
        while k < n and value[k].isspace():
            k += 1
        if k < n and value[k] == "(":
            i = k
        if i < n and value[i] == "(":
            end, quotes = _group_end_checked(value, i, quotes)
            if end is None:
                # unclosed: the pattern keeps its "(", so it is never unrestricted
                names.append((name, value[i:]))
                break
            names.append((name, value[i + 1 : end - 1]))
            i = end
        else:
            names.append((name, None))
    return names


def grants_unrestricted_bash(allowed: object) -> bool:
    """Whether an ``allowed-tools`` value (a string, or a YAML list of them) grants ``Bash`` for every command."""
    items = allowed if isinstance(allowed, list) else [allowed]
    return any(
        name == "Bash" and is_unrestricted(pattern)
        for item in items
        if isinstance(item, str)
        for name, pattern in _tool_entries(item)
    )


def unrestricted_bash_message(risky: list[str]) -> str:
    """The finding's message, naming the risky-command codes the same skill has."""
    tail = f"; the skill also has {', '.join(risky)}, so those commands run with no prompt" if risky else ""
    return (
        "allowed-tools grants Bash for every command (no pattern, or Bash(*)); scope it, e.g. "
        f"Bash(git:*) Bash(python3 scripts/*){tail}"
    )
