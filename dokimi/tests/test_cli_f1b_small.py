# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The run_name, force, and policy concepts' moves (issue #94 F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import argparse

import pytest

from dokimi import cli, cli_flags, cli_routes
from dokimi.cli import build_parser

_MOVED = {"--run-id", "--batch", "--force-regen", "--stats-correction"}
_MOVES = [m for m in cli_flags.FLAG_MOVES if m.old in _MOVED]
_CASES = [(m.old, m.new, leaf) for m in _MOVES for leaf in m.leaves]
_VALUE = {"--force-regen": None, "--force": None, "--stats-correction": "bh", "--correction": "bh"}
# the arguments each leaf needs to parse at all, so a parse failure means the flag, not a missing argument
_REQUIRED = {
    ("answers", "generate"): ["--product", "p", "--questions", "q.json"],
    ("eval", "score", "pointwise"): ["--product", "p", "--answers", "a.json"],
    ("report", "eval"): ["--product", "p", "--eval", "e.json"],
    ("repo", "run"): ["src", "--arm", "x", "--model", "m"],
    ("benchmark", "run"): ["--library", "x"],
    ("benchmark", "batch"): ["--libraries", "x"],
    ("arms", "run"): ["--product", "p", "--questions", "q.json", "--arms", "a.yaml"],
    ("arms", "matrix-run"): ["--product", "p", "--questions", "q.json", "--arms", "a.yaml",
                             "--matrix-cells", "c.json"],
    ("report", "model-compare"): ["--run-ids", "a,b", "--out", "o.md"],
}


def _leaves_with(spelling: str) -> dict[tuple[str, ...], argparse.Action]:
    """Each leaf of the CLI tree (its argv path) that has ``spelling``, with the action behind it."""
    found: dict[tuple[str, ...], argparse.Action] = {}

    def walk(node: argparse.ArgumentParser, path: tuple[str, ...]) -> None:
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    walk(sub, (*path, name))
            elif spelling in action.option_strings:
                found[path] = action

    walk(build_parser(), ())
    return found


def _tail(spelling: str, value: str = "x") -> list[str]:
    value = _VALUE.get(spelling, value)
    return [spelling] if value is None else [spelling, value]


def _argv(leaf: tuple[str, ...], *tokens: str) -> list[str]:
    """``leaf`` with its required arguments, then ``tokens``."""
    return [*leaf, *_REQUIRED.get(leaf, []), *tokens]


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


def _id(value) -> str:
    return " ".join(value) if isinstance(value, tuple) else value


@pytest.mark.parametrize(("old", "new", "leaf"), _CASES, ids=_id)
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, leaf, capsys):
    text = _tombstone(_argv(leaf, *_tail(old)), capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join(_argv(leaf, *_tail(new))) in text


@pytest.mark.parametrize(("old", "new", "leaf"), _CASES, ids=_id)
def test_the_new_spelling_is_not_moved(old, new, leaf):
    assert cli_routes.moved_flags_argv(_argv(leaf, *_tail(new))) is None


@pytest.mark.parametrize(("old", "leaf"), [
    ("--run-id", ("repo", "run")),  # --batch's leaf, not --run-id's
    ("--batch", ("answers", "generate")),  # --run-id's leaf, not --batch's
    ("--force-regen", ("answers", "generate")),  # no such flag on this leaf
    ("--stats-correction", ("benchmark", "run")),  # no such flag on this leaf
])
def test_an_old_spelling_is_not_moved_on_a_leaf_that_never_had_it(old, leaf):
    assert cli_routes.moved_flags_argv(_argv(leaf, *_tail(old))) is None


@pytest.mark.parametrize(("argv", "dest", "value"), [
    (_argv(("answers", "generate"), "--name", "gpt4o"), "run_id", "gpt4o"),
    (_argv(("eval", "score", "pointwise"), "--name", "gpt4o"), "run_id", "gpt4o"),
    (_argv(("report", "eval"), "--name", "gpt4o"), "run_id", "gpt4o"),
    (_argv(("repo", "run"), "--name", "b"), "batch", "b"),
    (_argv(("benchmark", "run"), "--force"), "force_regen", True),
    (_argv(("arms", "run"), "--correction", "bh"), "stats_correction", "bh"),
    (_argv(("report", "model-compare"), "--correction", "bh"), "stats_correction", "bh"),
])
def test_a_moved_flag_keeps_its_destination(argv, dest, value):
    assert getattr(build_parser().parse_args(argv), dest) == value


@pytest.mark.parametrize(("old", "new", "leaf"), _CASES, ids=_id)
def test_a_moved_flag_parses_to_the_same_namespace_as_its_new_spelling(old, new, leaf):
    new_argv = cli_routes.moved_flags_argv(_argv(leaf, *_tail(old)))
    assert new_argv is not None
    assert build_parser().parse_args(new_argv) == build_parser().parse_args(_argv(leaf, *_tail(new)))


@pytest.mark.parametrize("move", _MOVES, ids=lambda m: f"{m.old}->{m.new}")
def test_a_moved_spelling_is_gone_from_every_leaf(move):
    assert _leaves_with(move.old) == {}


@pytest.mark.parametrize("move", _MOVES, ids=lambda m: f"{m.old}->{m.new}")
def test_every_leaf_of_a_move_has_the_new_spelling(move):
    assert set(move.leaves) <= set(_leaves_with(move.new))


@pytest.mark.parametrize("leaf", [leaf for m in _MOVES if m.new == "--name" for leaf in m.leaves])
def test_name_help_shows_the_registry_metavar(leaf):
    assert _leaves_with("--name")[leaf].metavar == "NAME"


@pytest.mark.parametrize("leaf", [leaf for m in _MOVES if m.new == "--correction" for leaf in m.leaves])
def test_correction_takes_only_holm_or_bh_and_defaults_to_holm(leaf):
    action = _leaves_with("--correction")[leaf]
    assert action.choices == ["holm", "bh"]
    assert action.default == "holm"


@pytest.mark.parametrize("leaf", [leaf for m in _MOVES if m.new == "--force" for leaf in m.leaves])
def test_force_is_a_switch_on_every_leaf_it_moved_to(leaf):
    assert isinstance(_leaves_with("--force")[leaf], argparse._StoreTrueAction)


def test_correction_rejects_a_value_outside_the_registry_choices():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(_argv(("arms", "run"), "--correction", "bonferroni"))
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", [
    _argv(("answers", "generate"), "--doc-source", "none"),
    _argv(("answers", "generate"), "--context7-id", "/x/y"),
    _argv(("report", "model-compare")),  # --run-ids stays: a list of labels, not one run's name
])
def test_kept_spellings_stay_as_they_are(argv):
    assert cli_routes.moved_flags_argv(argv) is None
    build_parser().parse_args(argv)
