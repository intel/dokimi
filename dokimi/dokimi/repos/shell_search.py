# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The files a ``grep``, ``egrep``, ``fgrep``, ``rg`` or ``git grep`` command line searches and shows lines of, for
``guidance_searched`` (contract §4.1). The walker of :mod:`~dokimi.repos.cli_traces` calls it for each command word it
follows; the option tables are :mod:`~dokimi.repos.probes`'s."""

from __future__ import annotations

from collections.abc import Sequence

from .probes import search_words

_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg"})
# the options after which a search prints no line of a file: quiet, only the names of files, only counts
_SHOWS_NO_LINE = frozenset({"--quiet", "--silent", "--files-with-matches", "--files-without-match", "--files", "--count",
                            "--count-matches", "--name-only"})
_SHORT_NO_LINE = {"rg": "qlc"}  # rg's -L follows links; for the others it lists the files without a match
_SHORT_NO_LINE_DEFAULT = "qlLc"


class Searched(str):
    """A path a search named: the walker keeps it among the files its printing commands read, and tells them apart by type."""


def search_program(name: str, args: Sequence[str]) -> tuple[str, Sequence[str]] | None:
    """``(program, its arguments)`` for a command that searches files, else None. ``git grep`` is ``git`` and ``grep``
    first: ``git -C dir grep`` is not followed, as ``-C`` moves the directory."""
    if name == "git" and args[:1] == ["grep"]:
        return "grep", args[1:]
    return (name, args) if name in _SEARCHERS else None


def searched_files(program: str, args: Sequence[str]) -> list[str]:
    """The operands of a search that prints lines of them (the pattern is none, nor a pattern file, nor stdin ``-``).

    None for a run that prints no line of a file: ``-q``, ``-l``, ``-L`` (not rg's), ``-c``, ``--files`` and the long
    forms. A directory operand (``rg x docs``) is returned as it is: it matches no guidance path."""
    operands, _, flags = search_words(program, args)
    short = _SHORT_NO_LINE.get(program, _SHORT_NO_LINE_DEFAULT)
    if any(f in _SHOWS_NO_LINE or (len(f) == 2 and f[1] in short) for f in flags):
        return []
    return [o for o in operands if o != "-"]
