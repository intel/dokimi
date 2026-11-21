# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The output concept's moves (issue #94 §2.4, F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import argparse

import shlex

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
    # `repo validate --out FILE` wrote the report; the report is --json now, the file is the redirect
    (["repo", "validate", "src", "--out", "r.json"], ["eval", "check", "--schema-only", "src"]),
    (["repo", "validate", "src", "--out"], ["eval", "check", "--schema-only", "src"]),  # no value: still dropped
    (["repo", "validate", "src", "--ou", "r.json"], ["eval", "check", "--schema-only", "src"]),  # argparse prefix
    (["repo", "validate", "src", "--cache", "c"], ["eval", "check", "--schema-only", "src", "--cache-dir", "c"]),
    # the retired parser's prefixes resolve to its own options, not to the new leaf's (--j was --json only;
    # --jobs is --concurrency on eval check; --c was --cache-dir only, --cache and --cache-root collide too)
    (["repo", "validate", "src", "--j"], ["eval", "check", "--schema-only", "src", "--json"]),
    (["repo", "validate", "src", "--c", "c"], ["eval", "check", "--schema-only", "src", "--cache-dir", "c"]),
    (["repo", "validate", "src", "--o", "r.json"], ["eval", "check", "--schema-only", "src"]),
    (["repo", "validate", "src", "--f", "warn"], ["eval", "check", "--schema-only", "src", "--fail-on", "warn"]),
    (["repo", "validate", "src", "--manifest-f", "m.yaml"],
     ["eval", "check", "--schema-only", "src", "--manifest-file", "m.yaml"]),
    # a value option takes the next token only when that token is not an option: `--out` with none is
    # dropped, and the option after it is still printed (the retired parser refused the whole argv)
    (["repo", "validate", "src", "--out", "--fail-on-warning"],
     ["eval", "check", "--schema-only", "src", "--fail-on=warn"]),
    (["repo", "validate", "src", "--out", "--json"], ["eval", "check", "--schema-only", "src", "--json"]),
    # argparse takes a negative number as a value (the retired parser had no option that looks like one)
    (["repo", "validate", "src", "--out", "-1"], ["eval", "check", "--schema-only", "src"]),
    # a `--` ends the options: a source named --out after it stays the source
    (["repo", "validate", "--", "--out"], ["eval", "check", "--schema-only", "--", "--out"]),
    (["repo", "validate", "src", "--", "--out", "r.json"],
     ["eval", "check", "--schema-only", "src", "--", "--out", "r.json"]),
    (["explore", "list", "--json"], ["eval", "tasks", "list", "--format", "localization"]),  # --json is no flag of the new leaf
])
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert " ".join(new) in text
    assert text == "moved: dokimi " + " ".join(new) + "\n"  # exact: a stray token after the hint fails too
    assert cli_flags.registry_problems() == []


@pytest.mark.parametrize(("token", "kept"), [
    ("-report file.json", []),
    ("--unknown=report file.json", []),
    ("--manifest-f=repo file.yaml", ["--manifest-file=repo file.yaml"]),
    ("--man=repo file.yaml", ["--man=repo file.yaml"]),  # ambiguous on the old parser
    ("-h file", ["-h file"]),
    ("-report\tfile.json", ["-report\tfile.json"]),  # argparse recognizes a literal space only
])
def test_retired_out_distinguishes_quoted_paths_from_options(token, kept, capsys):
    text = _tombstone(["repo", "validate", "src", "--out", token], capsys)
    assert shlex.split(text.removeprefix("moved: ")) == ["dokimi", "eval", "check", "--schema-only", "src", *kept]


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


def test_eval_check_j_prefix_is_json_only():
    # --jobs is --concurrency now, so --j has one match on eval check; no hidden alias keeps it for the old name
    assert cli.parse_args(["eval", "check", "src", "--j", "--schema-only"]).json is True


@pytest.mark.parametrize(("old", "new"), [
    (["repo", "validate", "src", "--fail-on-warning"], ["eval", "check", "--schema-only", "src", "--fail-on=warn"]),
    (["repo", "budget", "src", "--fail-on-warning"], ["repo", "budget", "src", "--fail-on=warn"]),
    (["repo", "scan", "src", "--fail-on-fail"], ["repo", "scan", "src", "--fail-on=fail"]),
    (["repo", "inspect", "src", "--fail-on-error"], ["repo", "inspect", "src", "--fail-on=fail"]),
    (["eval", "triggers", "generate", "--catalog", "c", "--strict"],
     ["eval", "triggers", "generate", "--catalog", "c", "--fail-on=warn"]),
    # a retired command and a moved flag: the printed line is the final command
    (["catalog", "trigger-set", "--strict"], ["eval", "triggers", "generate", "--fail-on=warn"]),
])
def test_a_fail_on_spelling_tombstones_with_the_new_argv(old, new, capsys):
    assert " ".join(new) in _tombstone(old, capsys)


def test_a_fail_on_spelling_with_a_stray_value_suggests_no_malformed_replacement(capsys):
    # the old flag took no value: the suggestion is the valid new flag, not --fail-on=warn=1
    text = _tombstone(["repo", "budget", "src", "--fail-on-warning=1"], capsys)
    assert "--fail-on=warn" in text and "warn=1" not in text


@pytest.mark.parametrize("argv", [
    ["docs", "check", "--spec", "s.yaml", "--strict"],  # a hard gate, not a finding level: --strict stays
    ["catalog", "check", "--strict"],
])
def test_strict_stays_on_the_leaves_that_gate_a_verdict(argv):
    assert cli_routes.moved_flags_argv(argv) is None


def test_each_fail_on_leaf_takes_only_the_level_it_gates_on():
    parser = cli.build_parser()
    assert parser.parse_args(["repo", "budget", "src", "--fail-on", "warn"]).fail_on == "warn"
    assert parser.parse_args(["repo", "scan", "src", "--fail-on", "fail"]).fail_on == "fail"
    for leaf in (("eval", "check"), ("repo", "inspect")):  # both levels, as memory lint and repo security take them
        for level in ("warn", "fail"):
            assert parser.parse_args([*leaf, "src", "--fail-on", level]).fail_on == level
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["repo", "scan", "src", "--fail-on", "warn"])
    assert exc.value.code == 2
