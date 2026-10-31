# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#94 M1: the evaluation commands moved under ``eval``; each old path now tombstones.

Every old path answers with ``moved: dokimi <new argv>`` and exits 2, and never runs. Every new
path is a live leaf of the parser.
"""

from __future__ import annotations

import argparse
import shlex

import pytest

from dokimi import cli_routes
from dokimi.cli import build_parser, parse_args

# (old argv, new argv): the 21 moves of #94 §4 M1, including #214's two (`questions import`,
# `eval reference`).
MOVES = [
    (("tasks", "inspect"), ("eval", "inspect")),
    (("repo", "tasks", "check"), ("eval", "check")),
    (("personas", "discover"), ("eval", "personas", "discover")),
    (("personas", "approve"), ("eval", "personas", "validate")),
    (("questions", "generate"), ("eval", "questions", "generate")),
    (("questions", "analyze"), ("eval", "questions", "analyze")),
    (("questions", "refine"), ("eval", "questions", "refine")),
    (("questions", "panel-review"), ("eval", "questions", "review")),
    (("questions", "import"), ("eval", "import")),
    (("tasks", "list"), ("eval", "tasks", "list")),
    (("catalog", "trigger-set"), ("eval", "triggers", "generate")),
    (("eval", "score"), ("eval", "score", "pointwise")),
    (("eval", "pairwise"), ("eval", "score", "pairwise")),
    (("eval", "panel-score"), ("eval", "score", "panel")),
    (("eval", "checklist"), ("eval", "score", "checklist")),
    (("eval", "grounding"), ("eval", "score", "grounding")),
    (("eval", "ragas"), ("eval", "score", "ragas")),
    (("eval", "reference"), ("eval", "score", "reference")),
    (("eval", "judge-validity"), ("eval", "judge", "validate")),
    (("eval", "judge-wording"), ("eval", "judge", "wording")),
    (("eval", "cross-judge"), ("eval", "judge", "agreement")),
]


# The retired roots: a bare root (or its help) names the new group. Not leaves, so not in MOVES.
ROOT_MOVES = [
    (("personas",), ("eval", "personas")),
    (("questions",), ("eval", "questions")),
]


def _parser_at(path: tuple[str, ...]) -> argparse.ArgumentParser | None:
    parser = build_parser()
    for word in path:
        sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None or word not in sub.choices:
            return None
        parser = sub.choices[word]
    return parser


def _is_leaf(parser: argparse.ArgumentParser) -> bool:
    return not any(isinstance(a, argparse._SubParsersAction) for a in parser._actions)


def test_the_tombstone_table_holds_exactly_the_moves():
    assert {(tomb.old, tomb.new) for tomb in cli_routes.TOMBSTONES} == set(MOVES) | set(ROOT_MOVES)
    assert len(MOVES) == 21


@pytest.mark.parametrize(("old", "new"), ROOT_MOVES, ids=[" ".join(old) for old, _ in ROOT_MOVES])
@pytest.mark.parametrize("tail", [[], ["--help"], ["generate", "x.json"]], ids=["bare", "help", "leaf"])
def test_a_retired_root_names_its_new_group(old, new, tail, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*old, *tail])
    assert exc.value.code == 2
    assert f"moved: dokimi {shlex.join(new)}" in capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), MOVES, ids=[" ".join(old) for old, _ in MOVES])
def test_an_old_path_exits_2_and_names_the_new_argv(old, new, capsys):
    # A positional after the old path: a bare `eval score` is the group's help, not an old call.
    with pytest.raises(SystemExit) as exc:
        parse_args([*old, "data.json"])
    assert exc.value.code == 2
    assert f"moved: dokimi {shlex.join(new)}" in capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), MOVES, ids=[" ".join(old) for old, _ in MOVES])
def test_an_old_path_keeps_its_arguments_when_it_tombstones(old, new, capsys):
    argv = [*old, "--answers", "a.json", "pos"]
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2
    assert f"moved: dokimi {shlex.join([*new, '--answers', 'a.json', 'pos'])}" in capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), MOVES, ids=[" ".join(old) for old, _ in MOVES])
def test_the_new_path_is_a_live_leaf(old, new):
    parser = _parser_at(new)
    assert parser is not None, shlex.join(new)
    assert _is_leaf(parser), shlex.join(new)
    assert cli_routes.moved_argv(list(new)) is None


def test_the_retired_roots_are_gone_from_the_parser():
    top = build_parser()
    sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
    assert "personas" not in sub.choices
    assert "questions" not in sub.choices
    # A bare `questions` is a tombstone, not a route: it names `eval questions` and exits 2.
    with pytest.raises(SystemExit) as exc:
        parse_args(["questions"])
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", [["eval", "score"], ["eval", "score", "--help"]])
def test_the_group_that_stays_answers_its_own_help(argv):
    # `eval score` is still a group: bare, or with only help, it is not an old call to move.
    assert cli_routes.moved_argv(argv) is None


@pytest.mark.parametrize("argv", [["eval", "score", "--answers=a.json"], ["eval", "score", "-x"]])
def test_an_old_call_with_only_flags_still_tombstones(argv):
    # The equals form and any other option are old calls: they name the new command.
    assert cli_routes.moved_argv(argv) == ["eval", "score", "pointwise", *argv[2:]]


def test_eval_score_help_lists_its_leaves(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["eval", "score", "--help"])
    assert exc.value.code == 0
    assert "pointwise" in capsys.readouterr().out


def test_eval_score_with_a_leaf_is_not_a_tombstone():
    # `eval score` moved to `eval score pointwise`, so it is a group: its leaves are live argv.
    for leaf in ("pointwise", "pairwise", "panel", "checklist", "grounding", "ragas", "reference"):
        assert cli_routes.moved_argv(["eval", "score", leaf, "--x"]) is None, leaf
