# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#94 M2: the `skill`, `catalog`, `trace` and `data` commands moved; each old path now tombstones.

Every old path answers with ``moved: dokimi <new argv>`` and exits 2 without running anything.
Every new path is a live parser, and the route table lists it.
"""

from __future__ import annotations

import argparse
import shlex

import pytest

from dokimi import cli_routes
from dokimi.cli import build_parser, parse_args

# (old argv, new argv): the moves of #94 §4 that M2 makes. `novelty routing`/`view`,
# `library list|show`, `subjects list|show` and `repo tasks replay` stay where they are for now.
MOVES = [
    (("novelty", "analyze"), ("skill", "novelty", "analyze")),
    (("novelty", "atoms"), ("skill", "novelty", "atoms")),
    (("novelty", "probe"), ("skill", "novelty", "probe")),
    (("novelty", "items"), ("skill", "novelty", "items")),
    (("novelty", "shorten"), ("skill", "shorten")),
    (("catalog", "scan"), ("catalog", "check")),
    (("library", "intents"), ("catalog", "intents")),
    (("context", "trace"), ("trace", "inspect")),
    (("context", "consistency"), ("trace", "consistency")),
    (("context", "prefilter"), ("trace", "prefilter")),
    (("dataset", "export"), ("data", "export")),
    (("ingest", "docling"), ("data", "import", "docling")),
]

# The retired roots: a bare root (or its help) names the new group.
ROOT_MOVES = [
    (("context",), ("trace",)),
    (("dataset",), ("data", "export")),
    (("ingest",), ("data", "import")),
]


def _is_leaf(parser: argparse.ArgumentParser) -> bool:
    return not any(isinstance(a, argparse._SubParsersAction) for a in parser._actions)


def _parser_at(path: tuple[str, ...]) -> argparse.ArgumentParser | None:
    """The parser of argv ``path`` in the built CLI, or None when no such route is registered."""
    parser = build_parser()
    for word in path:
        sub = next(
            (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
        )
        if sub is None or word not in sub.choices:
            return None
        parser = sub.choices[word]
    return parser


@pytest.mark.parametrize(("old", "new"), MOVES + ROOT_MOVES)
def test_old_path_tombstones_with_the_new_argv(old, new, capsys):
    argv = [*old, "some-arg", "--flag"]
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("moved: dokimi " + shlex.join([*new, "some-arg", "--flag"])), err


@pytest.mark.parametrize(("old", "new"), MOVES + ROOT_MOVES)
def test_old_path_is_not_a_live_route(old, new):
    assert _parser_at(old) is None


@pytest.mark.parametrize(("old", "new"), MOVES)
def test_new_path_is_a_live_leaf(old, new):
    parser = _parser_at(new)
    assert parser is not None and _is_leaf(parser), f"{' '.join(new)} is not a leaf"
    assert cli_routes.moved_argv(list(new)) is None, f"{' '.join(new)} tombstones itself"
    assert cli_routes._leaf(new) is not None, f"{' '.join(new)} is not in the route table"


@pytest.mark.parametrize(("old", "new"), MOVES)
def test_new_path_shows_help(old, new, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*new, "--help"])
    assert exc.value.code == 0
    assert "usage: dokimi " + " ".join(new) in capsys.readouterr().out


STAYING = [
    ("novelty", "routing"),
    ("novelty", "view"),
    ("library", "list"),
    ("library", "show"),
    ("subjects", "list"),
    ("repo", "tasks", "replay"),
    ("tools", "preference"),
    ("data", "schema", "show"),
]


@pytest.mark.parametrize("path", STAYING, ids=[" ".join(path) for path in STAYING])
def test_groups_that_stay_keep_their_live_leaves(path, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*path, "--help"])
    assert exc.value.code == 0
    assert "usage: dokimi " + " ".join(path) in capsys.readouterr().out


def test_the_retired_roots_are_gone_from_the_parser():
    top = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    for root in ("context", "dataset", "ingest"):
        assert root not in top.choices
        assert root not in {r.name for r in cli_routes.ROOTS}


def test_new_roots_sit_in_their_sections():
    sections = {root.name: root.section for root in cli_routes.ROOTS}
    assert sections["skill"] == "Subjects"
    assert sections["trace"] == "Analysis and data"
    assert sections["data"] == "Analysis and data"


def test_tombstones_are_consistent():
    assert cli_routes.tombstone_problems() == []
