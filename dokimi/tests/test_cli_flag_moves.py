# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The output and tokens concepts' moves (issue #94 §2.4, F1b).

Old spellings tombstone, new ones parse the same.
"""

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


@pytest.mark.parametrize(("old", "new"), [
    # `--max-tokens` was retrieval on four leaves and the output ceiling on ten (the tokens concept, F1b)
    (["answers", "generate", "--product", "p", "--max-tokens", "8"],
     ["answers", "generate", "--product", "p", "--retrieval-tokens", "8"]),
    (["benchmark", "preflight", "--library", "x", "--max-tokens", "8"],
     ["benchmark", "preflight", "--library", "x", "--retrieval-tokens", "8"]),
    (["skill", "novelty", "atoms", "--max-tokens", "8"], ["skill", "novelty", "atoms", "--max-output-tokens", "8"]),
    (["tools", "preference", "--max-tokens", "8"], ["tools", "preference", "--max-output-tokens", "8"]),
    (["skill", "shorten", "--max-tokens", "8"], ["skill", "shorten", "--max-output-tokens", "8"]),
    # a retired command and a moved flag in one argv: the printed line is the final command
    (["novelty", "routing", "--max-tokens", "9"], ["skill", "routing", "--max-output-tokens", "9"]),
    (["skill", "shorten", "--budgets", "0.5"], ["skill", "shorten", "--token-budgets", "0.5"]),
])
def test_an_old_token_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert " ".join(new) in text


@pytest.mark.parametrize(("argv", "dest", "value"), [
    (["answers", "generate", "--product", "p", "--questions", "q", "--retrieval-tokens", "8"], "max_tokens", 8),
    (["skill", "novelty", "atoms", "--artifact", "skill:s", "--max-output-tokens", "8"],
     "max_tokens", 8),
    (["skill", "shorten", "--artifact", "skill:s", "--atoms", "a.json", "--token-budgets", "0.5", "0.25"],
     "budgets", [0.5, 0.25]),
])
def test_the_token_spellings_parse_to_the_same_value(argv, dest, value):
    assert getattr(cli.build_parser().parse_args(argv), dest) == value


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

    walk(cli.build_parser(), ())
    return found


@pytest.mark.parametrize("flag", [f for c in cli_flags.CONCEPTS if c.name == "tokens" for f in c.canonical])
def test_help_shows_the_registry_metavar_for_each_token_spelling(flag):
    # without a metavar argparse prints the dest, the old `MAX_TOKENS` / `BUDGETS`
    leaves = _leaves_with(flag.spelling)
    assert leaves
    assert {path: action.metavar for path, action in leaves.items()} == {
        path: flag.value.split()[0] for path in leaves}


_TOKEN_MOVES = [m for m in cli_flags.FLAG_MOVES if m.old in ("--max-tokens", "--budgets")]


@pytest.mark.parametrize("move", _TOKEN_MOVES, ids=lambda m: f"{m.old}->{m.new}")
def test_a_token_move_lists_exactly_the_leaves_that_have_the_new_spelling(move):
    # a leaf missing here fails with a bare `unrecognized arguments` instead of a `moved:` line
    assert sorted(move.leaves) == sorted(_leaves_with(move.new))


@pytest.mark.parametrize(("old", "new", "leaf"), [
    (m.old, m.new, leaf) for m in _TOKEN_MOVES for leaf in m.leaves])
def test_every_token_move_tombstones_on_each_of_its_leaves(old, new, leaf, capsys):
    text = _tombstone([*leaf, old, "8"], capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join([*leaf, new, "8"]) in text


@pytest.mark.parametrize("leaf", sorted(_leaves_with("--retrieval-tokens")))
@pytest.mark.parametrize("value", ["0", "-5"])
def test_every_retrieval_leaf_refuses_a_count_below_one(leaf, value):
    with pytest.raises(argparse.ArgumentTypeError):
        _leaves_with("--retrieval-tokens")[leaf].type(value)
