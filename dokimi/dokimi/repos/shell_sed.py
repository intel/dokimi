# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The files a ``sed -n`` command prints, for :func:`~dokimi.repos.cli_traces.shell_reads`: its options and script are read
here (``-n``, ``-e``, ``-f``, an in-place edit, a script that only prints lines)."""

from __future__ import annotations

import re

_PRINT_ONLY = re.compile(r"\s*(?:(?:\d+|\$)(?:\s*,\s*(?:\d+|\$))?\s*)?p\s*")  # ``p``, ``5p``, ``1,80p``, ``$p``: prints, writes nothing


class _SedScan:
    """What the options of a ``sed`` command line say: ``-n`` (quiet), the scripts given by ``-e`` / ``--expression``,
    a script file (``-f`` / ``--file``, not read here), an in-place edit, and which option takes the next word."""

    def __init__(self) -> None:
        self.quiet = self.in_place = self.script_file = False
        self.scripts: list[str] = []
        self.takes = ""  # the option whose value is the next word: "e", "f", "l" (line length), or "" for none

    def add(self, kind: str, value: str) -> None:
        if kind == "e":
            self.scripts.append(value)
        elif kind == "f":
            self.script_file = True

    def long(self, a: str) -> None:
        name, eq, value = a.partition("=")
        if name == "--in-place":
            self.in_place = True
        self.quiet |= name in ("--quiet", "--silent")
        kind = {"--expression": "e", "--file": "f", "--line-length": "l"}.get(name, "")
        if kind and eq:
            self.add(kind, value)
        elif kind:
            self.takes = kind  # the value is the next word

    def short(self, a: str) -> None:
        """A cluster of short options: -n, -ne, -nE, -i.bak, -e1p."""
        for i, c in enumerate(a[1:], 1):
            if c == "i":
                self.in_place = True
                return
            self.quiet |= c == "n"
            if c in "efl":
                if i < len(a) - 1:  # attached: -e1p
                    self.add(c, a[i + 1:])
                else:
                    self.takes = c  # the value is the next word
                return


def sed_files(args: list[str]) -> list[str]:
    """Files of ``sed -n <script> files``, for a script that only prints (``p`` with line addresses). Nothing for an
    in-place edit, a run without ``-n``, a script file (not read), or any other script (``d``, ``w``, ``s``...)."""
    scan = _SedScan()
    positional = _sed_positional(scan, args)
    if positional is None or not scan.quiet or scan.script_file:
        return []
    scripts, files = (scan.scripts, positional) if scan.scripts else (positional[:1], positional[1:])
    if not all(_print_only(script) for script in scripts):
        return []  # not a print-only script: its files are not read as printed
    return [f for f in files if f != "-"]  # `-` is stdin: the script still counts it as a position


def _sed_positional(scan: _SedScan, args: list[str]) -> list[str] | None:
    """The operands of a sed command line, its options read into ``scan``; None for an in-place edit."""
    ended = False
    positional: list[str] = []
    for a in args:
        if scan.takes:
            scan.add(scan.takes, a)
            scan.takes = ""
        elif ended or not a.startswith("-") or a == "-":
            positional.append(a)
        elif a == "--":
            ended = True
        elif a.startswith("--"):
            scan.long(a)
        else:
            scan.short(a)
        if scan.in_place:
            return None
    return positional


def _print_only(script: str) -> bool:
    return all(not piece.strip() or _PRINT_ONLY.fullmatch(piece) for piece in re.split(r"[;\n]", script))
