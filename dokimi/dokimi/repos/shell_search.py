# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The files a ``grep``, ``egrep``, ``fgrep``, ``rg`` or ``git grep`` command line searches and shows lines of, for
``guidance_searched`` (contract §4.1). The walker of :mod:`~dokimi.repos.shell_walk` calls it for each command word it
follows; the option tables are :mod:`~dokimi.repos.probes`'s."""

from __future__ import annotations

import re
import shlex
from collections.abc import Container, Sequence
from typing import Any

from .probes import search_words
from .shell_words import _ASSIGN, _program
from .transcript import relative_path

_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg"})
# the options after which a search prints no line of a file: quiet, only the names of files, only counts
_SHOWS_NO_LINE = frozenset({"--quiet", "--silent", "--files-with-matches", "--files-without-match", "--files", "--count",
                            "--count-matches", "--name-only"})
_SHORT_NO_LINE = {"rg": "qlchV"}  # rg's -L follows links and -h is --help; for the others -L lists the files without a match
_SHORT_NO_LINE_DEFAULT = "qlLcV"  # -V prints the version; grep's -h is --no-filename, a search


class Searched(str):
    """A path a search named: the walker keeps it among the files its printing commands read, and tells them apart by type."""


_CUT_LONG = {"head": ("bytes", "lines", "quiet", "silent", "verbose", "zero-terminated", "help", "version"),
             "tail": ("bytes", "lines", "quiet", "silent", "verbose", "zero-terminated", "help", "version", "follow",
                      "retry", "pid", "sleep-interval", "max-unchanged-stats")}
_CUT_FLAGS = {"head": "qvz", "tail": "qvzf"}  # the short options that take no value
_CUT_VALUED = ("lines", "bytes", "pid", "sleep-interval", "max-unchanged-stats")  # the long ones that take one
# a count: digits, with the size suffix the GNU tools read (``b``, ``k``, ``KiB``, ``MB``, ...), after a sign for head/tail
_COUNT = r"([+-]?)(\d+)(?:[bkKmMGTPEZYRQ](?:iB|B|D)?)?"
_SECONDS = r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"  # tail's -s: a decimal number
# what the tools accept as the value of an option; one that does not fits ends the command before it prints a line
_VALUE_FORM = {"lines": _COUNT, "bytes": _COUNT, "pid": r"\d+", "sleep-interval": _SECONDS, "max-unchanged-stats": r"\d+"}
_Parsed =tuple[int, str | None] | None  # (the words an option takes, the line or byte count it sets) or None: not valid


def _valid_follow(value: str) -> bool:
    """Whether ``value`` is a mode ``tail --follow=`` takes on its input: ``descriptor`` or a prefix of it. ``name`` (or a
    prefix of it) is a mode the tool refuses for stdin, before it prints a line (``cannot follow '-' by name``)."""
    return bool(value) and "descriptor".startswith(value)


def _cut_long(program: str, words: list[str], i: int) -> _Parsed:
    """The long option ``words[i]`` of ``head`` or ``tail``, an unambiguous prefix of its name too (``--li=5``)."""
    name, eq, value = words[i][2:].partition("=")
    hits = [n for n in _CUT_LONG[program] if n == name] or [n for n in _CUT_LONG[program] if n.startswith(name)]
    if len(hits) != 1 or hits[0] in ("help", "version"):
        return None
    if hits[0] not in _CUT_VALUED:  # a flag takes no value; only tail's ``--follow`` has an optional attached one
        if hits[0] == "follow" and eq and not _valid_follow(value):
            return None
        return (1, None) if not eq or hits[0] == "follow" else None
    given = value if eq else (words[i + 1] if i + 1 < len(words) else "")  # else the value is the next word
    if not re.fullmatch(_VALUE_FORM[hits[0]], given):
        return None
    return (1 if eq else 2), (given if hits[0] in ("lines", "bytes") else None)


def _cut_short(program: str, words: list[str], i: int) -> _Parsed:
    """The cluster of short options ``words[i]``: flags, then maybe ``n`` or ``c`` (a count) or tail's ``s``, which takes
    the rest of the word or the next one."""
    word = words[i]
    for at, c in enumerate(word[1:], 2):
        if c in "nc" or (c == "s" and program == "tail"):
            attached = word[at:]
            value = attached or (words[i + 1] if i + 1 < len(words) else "")
            if not re.fullmatch(_SECONDS if c == "s" else _COUNT, value):
                return None
            return (1 if attached else 2), (value if c != "s" else None)
        if c not in _CUT_FLAGS[program]:
            return None
    return 1, None


def _cut_count(program: str, words: list[str]) -> str | None:
    """The count of lines (or bytes) a ``head`` or ``tail`` word list passes on, 10 by default; None when an option is
    unknown, ``--help`` or ``--version``, or a word is a file the command reads instead of its input."""
    count, i = "10", 1
    while i < len(words):
        word = words[i]
        if word == "-":  # the input named explicitly
            i += 1
            continue
        if word == "--":  # the end of the options: only ``-`` may follow
            return count if all(w == "-" for w in words[i + 1:]) else None
        if word.startswith("--"):
            parsed = _cut_long(program, words, i)
        elif re.fullmatch(r"-\d+", word):
            parsed = 1, word[1:]
        elif word.startswith("-") and len(word) > 1 and word[1] != "-":
            parsed = _cut_short(program, words, i)
        else:
            return None
        if parsed is None:
            return None
        i, count = i + parsed[0], count if parsed[1] is None else parsed[1]
    return count


def cuts_lines(stage: str, functions: Container[str] = ()) -> bool:
    """Whether the pipeline stage ``stage`` is a ``head`` or ``tail`` that passes some of the lines it is fed on: its options
    are known ones (``-n 20``, ``-5``, ``-c10``, ``--lines=5``) and name no file, its count is not zero (``head -n 0``;
    ``tail -n +0`` is all of them), it is no ``--help`` or ``--version``, and the shell has no function of that name."""
    try:
        words = shlex.split(stage)
    except ValueError:
        return False
    while words and _ASSIGN.match(words[0]):  # ``LC_ALL=C head``: a variable set for the command
        words = words[1:]
    program = _program(words[0]) if words else ""  # ``/usr/bin/head`` is the system's, ``./head`` is not
    if program not in _CUT_FLAGS or words[0] in functions:
        return False
    words = [program, *words[1:]]
    count = _cut_count(program, words)
    match = re.fullmatch(_COUNT, count or "")
    if match is None:
        return False
    sign, digits = match.groups()
    return any(int(c) for c in digits) or (sign == "-" if words[0] == "head" else sign == "+")


def searched_field(searched: list[str], workdir: str | None) -> dict[str, Any]:
    """The ``searched`` field of a command event: the workspace paths its searches showed lines of; none without any."""
    return {"searched": [relative_path(p, workdir) for p in searched]} if searched else {}


def search_program(name: str, args: Sequence[str]) -> tuple[str, Sequence[str]] | None:
    """``(program, its arguments)`` for a command that searches files, else None. ``git grep`` is ``git`` and ``grep``
    first, after the global options that leave the directory alone (``--no-pager``, ``-P``, ``-c key=value``):
    ``git -C dir grep`` is not followed, as ``-C`` moves the directory. Its program is ``git grep``, with git's own
    options that take a word (``--max-depth 0``), not GNU grep's."""
    if name == "git":
        rest = list(args)
        while rest[:1] in (["--no-pager"], ["-P"]) or (rest[:1] == ["-c"] and len(rest) > 1):
            rest = rest[2:] if rest[0] == "-c" else rest[1:]
        if rest[:1] == ["grep"]:
            return "git grep", rest[1:]
    return (name, args) if name in _SEARCHERS else None


def passes_through(program: str, args: Sequence[str]) -> bool:
    """Whether the search is ``rg --passthru`` (``--passthrough``): it prints every line of a file, matching or not, and
    still exits 1 when none matched, so that exit says nothing about what it showed."""
    return program == "rg" and bool({"--passthru", "--passthrough"} & search_words(program, args)[2])


def searched_files(program: str, args: Sequence[str]) -> list[str]:
    """The operands of a search that prints lines of them (the pattern is none, nor a pattern file, nor stdin ``-``).

    None for a run that prints no line of a file: ``-q``, ``-l``, ``-L`` (not rg's), ``-c``, ``--files`` and the long
    forms, ``--help`` and ``--version`` as options (``grep -e --help f`` searches for the text), a match limit of zero
    (``-m0``, ``-m +0``), and ``git grep`` of a revision (``git grep x HEAD~1 -- f``), which shows an older blob, not the
    workspace file. A directory operand (``rg x docs``) is returned as it is: it matches no guidance path."""
    operands, _, flags, limit = search_words(program, args)
    if program == "git grep" and "--" in args and search_words(program, args[:list(args).index("--")])[0]:
        return []  # ``git grep x HEAD~1 -- AGENTS.md``: a word before ``--`` is a revision, and the lines are its blob's
    short = _SHORT_NO_LINE.get(program, _SHORT_NO_LINE_DEFAULT)
    if any(f in _SHOWS_NO_LINE or (len(f) == 2 and f[1] in short) for f in flags):
        return []
    digits = limit[1:] if limit is not None and limit.startswith("+") else limit  # strtoimax and Rust's parse take a leading +
    if "--help" in flags or "--version" in flags or (digits is not None and digits.isdecimal() and not any(int(c) for c in digits)):
        return []
    return [o for o in operands if o != "-"]
