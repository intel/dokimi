# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every CLI command and subcommand renders ``--help``.

Building the parser does not format any help text; argparse does that only when asked, so a
``%`` in a help string, a bad ``metavar`` or a ``choices`` that cannot be rendered surfaces
only when a user types ``--help`` on that one subcommand. Most command modules are covered
well below the package average, so nothing else in the suite would see it either.
"""

from __future__ import annotations

import argparse

import pytest

from dokimi.cli import build_parser


def _command_paths(parser: argparse.ArgumentParser, prefix=()):
    yield prefix
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            seen: set[int] = set()
            for name, sub in action.choices.items():
                if id(sub) in seen:  # an alias (`product` for `library`): same parser
                    continue
                seen.add(id(sub))
                yield from _command_paths(sub, prefix + (name,))


PATHS = list(_command_paths(build_parser()))


def test_the_walk_finds_the_command_tree():
    top = {p[0] for p in PATHS if len(p) == 1}
    assert {"docs", "eval", "tasks", "novelty", "repo"} <= top
    assert any(len(p) == 2 for p in PATHS)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: " ".join(p) or "<root>")
def test_help_renders(path, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([*path, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: ")
    assert " ".join(path) in out.splitlines()[0]
