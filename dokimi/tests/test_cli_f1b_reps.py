# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The reps and concurrency concepts' moves (issue #94 §2.4, F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import re

import pytest

from dokimi import cli, cli_routes
from dokimi.cli import build_parser


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), [
    # reps: repetitions of one cell
    (["tasks", "run", "--repeats", "3"], ["tasks", "run", "--reps", "3"]),
    (["subjects", "run", "s.toml", "--work-repeats=2"], ["subjects", "run", "s.toml", "--reps=2"]),
    (["benchmark", "run", "--library", "x", "--multi-run", "3"], ["benchmark", "run", "--library", "x", "--reps", "3"]),
    (["catalog", "trigger-probe", "--trigger-set", "t.json", "--trials", "3"],
     ["catalog", "trigger-probe", "--trigger-set", "t.json", "--reps", "3"]),
    # concurrency: how much runs at once; `-j` stays
    (["repo", "run", "src", "--jobs", "2"], ["repo", "run", "src", "--concurrency", "2"]),
    (["eval", "check", "src", "--jobs=2"], ["eval", "check", "src", "--concurrency=2"]),
    (["catalog", "check", "--per-skill", "--jobs", "2"], ["catalog", "check", "--per-skill", "--concurrency", "2"]),
])
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join(new) in text


@pytest.mark.parametrize("argv", [
    ["tasks", "run", "--reps", "3"],
    ["subjects", "run", "s.toml", "--reps", "2"],
    ["repo", "run", "src", "-j", "2"],  # the short form stays canonical
    ["repo", "run", "src", "--concurrency", "2"],
    ["catalog", "check", "--per-skill", "--concurrency", "2"],
    ["repo", "run", "--", "--jobs", "2"],  # after `--` every token is a positional, not a flag
])
def test_a_canonical_spelling_is_not_moved(argv):
    assert cli_routes.moved_flags_argv(argv) is None


@pytest.mark.parametrize(("old", "dest", "value"), [
    (["tasks", "run", "--harness", "h", "--all", "--repeats", "3"], "repeats", 3),
    (["benchmark", "run", "--library", "x", "--multi-run", "3"], "multi_run", 3),
    (["catalog", "trigger-probe", "--catalog", "c", "--out", "o.json", "--trigger-set", "t.json", "--trials", "3"],
     "trials", 3),
])
def test_a_moved_reps_flag_parses_to_the_same_value_as_its_new_spelling(old, dest, value):
    new = cli_routes.moved_flags_argv(old)
    assert new is not None
    assert getattr(build_parser().parse_args(new), dest) == value


def test_a_moved_concurrency_flag_parses_to_the_same_destination_as_its_new_spelling():
    required = ["src", "--arm", "x", "--model", "m", "--name", "b"]
    new = cli_routes.moved_flags_argv(["repo", "run", *required, "--jobs", "2"])
    assert new is not None
    assert build_parser().parse_args(new).jobs == 2
    assert build_parser().parse_args(["repo", "run", *required, "-j", "2"]).jobs == 2


@pytest.mark.parametrize("argv", [
    ["tasks", "run", "--harness", "h", "--all", "--jobs", "2"],  # --jobs never existed on tasks run
    ["subjects", "run", "s.toml", "--repeats", "2"],  # --repeats was tasks run's only
])
def test_an_old_spelling_is_not_moved_on_a_leaf_that_never_had_it(argv):
    assert cli_routes.moved_flags_argv(argv) is None


@pytest.mark.parametrize(("argv", "usage"), [
    (["tasks", "run"], "--reps N"),
    (["subjects", "run"], "--reps N"),
    (["benchmark", "run"], "--reps N"),
    (["catalog", "trigger-probe"], "--reps N"),
    (["repo", "run"], "--concurrency(?: N)?, -j N"),
    (["eval", "check"], "--concurrency(?: N)?, -j N"),
    (["catalog", "check"], "--concurrency N"),
])
def test_help_shows_the_registry_metavar_not_the_old_name(argv, usage, capsys):
    """``usage`` is a pattern: before Python 3.13 argparse repeats the metavar after each option string."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args([*argv, "--help"])
    assert exc.value.code == 0
    assert re.search(usage, capsys.readouterr().out)
