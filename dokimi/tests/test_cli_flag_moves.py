# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The output concept's moves (issue #94 §2.4, F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import argparse

import pytest

from dokimi import cli, cli_flags, cli_routes


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), [
    (["benchmark", "run", "--library", "x", "--output-dir", "d"],
     ["benchmark", "run", "--library", "x", "--out-dir", "d"]),
    (["arms", "run", "--out-json", "a.json"], ["arms", "run", "--out", "a.json"]),
    (["docs", "check", "--out-json=b.json"], ["docs", "check", "--out=b.json"]),
    (["repo", "run", "src", "--out", "runs"], ["repo", "run", "src", "--out-dir", "runs"]),
    (["eval", "check", "src", "--out", "w"], ["eval", "check", "src", "--out-dir", "w"]),
    (["eval", "tasks", "generate", "src", "--out", "t"],
     ["eval", "tasks", "generate", "src", "--out-dir", "t"]),
    # a retired command and a moved flag in one argv: the printed line is the final command
    (["repo", "tasks", "check", "src", "--out", "d"], ["eval", "check", "src", "--out-dir", "d"]),
])
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join(new) in text
    assert cli_flags.registry_problems() == []


@pytest.mark.parametrize("argv", [
    ["novelty", "atoms", "--out", "atoms.json"],  # a file on this leaf: `--out` is canonical
    ["repo", "scan", "src", "--out", "report.json"],
    ["repo", "run", "src", "--out-dir", "runs"],  # the new spelling is not rewritten
    ["docs", "check", "--out", "x.json"],
    ["repo", "scan", "--", "--out-json"],  # after `--` every token is a positional, not a flag
    ["repo", "run", "--", "--out", "d"],
])
def test_a_canonical_or_file_spelling_is_not_moved(argv):
    assert cli_routes.moved_flags_argv(argv) is None


def test_the_directory_move_applies_only_to_the_directory_leaves():
    # `--out` names a file on `novelty atoms` and a directory on `repo run`: each leaf keeps its own meaning.
    assert cli_routes.moved_flags_argv(["repo", "run", "src", "--out", "d"]) == [
        "repo", "run", "src", "--out-dir", "d"]
    assert cli_routes.moved_flags_argv(["novelty", "atoms", "--out", "f"]) is None


def test_a_moved_flag_before_the_delimiter_moves_and_the_positional_after_it_stays():
    assert cli_routes.moved_flags_argv(["repo", "run", "--out", "d", "--", "--out"]) == [
        "repo", "run", "--out-dir", "d", "--", "--out"]


def test_old_and_new_spellings_parse_to_the_same_value():
    bench_new = cli.build_parser().parse_args(["benchmark", "run", "--library", "x", "--out-dir", "d"])
    assert bench_new.output_dir == "d"
    docs_new = cli.build_parser().parse_args(["docs", "check", "--out", "b.json"])
    assert docs_new.out_json == "b.json"
    arms_new = cli.build_parser().parse_args(
        ["arms", "run", "--product", "p", "--questions", "q", "--arms", "baseline", "--out", "a.json"])
    assert arms_new.out_json == "a.json"


def test_add_refuses_a_spelling_the_concept_does_not_own():
    parser = argparse.ArgumentParser()
    cli_flags.add(parser, "output", "--out", default=None)
    with pytest.raises(ValueError, match="not a canonical spelling of `output`"):
        cli_flags.add(parser, "output", "--output")
    with pytest.raises(KeyError):
        cli_flags.add(parser, "no-such-concept", "--out")
