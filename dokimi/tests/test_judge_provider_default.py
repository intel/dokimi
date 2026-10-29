# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``--judge-provider`` without ``--judge-model`` gets a judge model that runs there."""

from __future__ import annotations

import argparse

import pytest

from dokimi.cli import build_parser, parse_args
from dokimi.defaults import (
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_JUDGE_MODEL,
    JUDGE_MODEL_FOR_PROVIDER,
    default_judge_model,
)


def _judge_commands() -> dict[str, list[str]]:
    """Every subcommand with ``--judge-provider`` but novelty -> argv with its required args."""
    found: dict[str, list[str]] = {}

    def walk(parser: argparse.ArgumentParser, path: list[str]) -> None:
        dests = {a.dest for a in parser._actions}
        if "judge_provider" in dests and path[0] != "novelty":
            argv = list(path)
            for action in parser._actions:
                if action.required:
                    argv += [action.option_strings[0], "x"] if action.option_strings else ["x"]
            found[" ".join(path)] = argv
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                seen: set[int] = set()
                for name, sub in action.choices.items():
                    if id(sub) not in seen:  # an alias maps to the same parser
                        seen.add(id(sub))
                        walk(sub, [*path, name])

    walk(build_parser(), [])
    return found


COMMANDS = _judge_commands()


def test_the_commands_that_take_a_judge_provider_are_all_covered():
    assert set(COMMANDS) == {
        "evaluate", "eval score", "eval judge-validity", "eval checklist", "eval judge-wording",
        "eval pairwise", "benchmark run", "benchmark preflight", "benchmark batch", "arms run",
        "arms matrix-run", "subjects run", "eval reference", "eval redteam",
    }


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_judge_command_defaults_the_model_to_its_provider(command):
    argv = COMMANDS[command]
    assert build_parser().parse_args(argv).judge_model is JUDGE_MODEL_FOR_PROVIDER
    assert parse_args([*argv, "--judge-provider", "openai"]).judge_model == DEFAULT_GENERATOR_MODEL


@pytest.mark.parametrize("provider, model", [
    ("anthropic", DEFAULT_JUDGE_MODEL),
    ("amazon-bedrock", DEFAULT_JUDGE_MODEL),
    ("openai", DEFAULT_GENERATOR_MODEL),
])
def test_a_provider_alone_takes_the_default_judge_that_runs_there(provider, model):
    args = parse_args([*COMMANDS["eval score"], "--judge-provider", provider])
    assert (args.judge_provider, args.judge_model) == (provider, model)
    assert type(args.judge_model) is str


def test_no_judge_flags_keep_the_default_judge():
    args = parse_args(COMMANDS["arms run"])
    assert (args.judge_provider, args.judge_model) == ("anthropic", DEFAULT_JUDGE_MODEL)
    assert type(args.judge_model) is str


@pytest.mark.parametrize("order", ["model-first", "provider-first"])
def test_an_explicit_judge_model_wins_in_either_order(order):
    flags = ["--judge-model", "gpt-6.1-sol", "--judge-provider", "anthropic"]
    if order == "provider-first":
        flags = flags[2:] + flags[:2]
    args = parse_args([*COMMANDS["benchmark run"], *flags])
    assert (args.judge_provider, args.judge_model) == ("anthropic", "gpt-6.1-sol")


def test_an_explicit_default_name_is_kept_on_another_provider():
    # Naming the default judge yourself is a choice, not a left-out flag.
    args = parse_args([*COMMANDS["eval score"], "--judge-provider", "openai",
                       "--judge-model", DEFAULT_JUDGE_MODEL])
    assert args.judge_model == DEFAULT_JUDGE_MODEL


@pytest.mark.parametrize("provider", ["google", "google-vertex", "openrouter"])
def test_a_provider_with_no_default_judge_exits_before_running(provider, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*COMMANDS["subjects run"], "--judge-provider", provider])
    assert exc.value.code == 2
    assert f"--judge-provider {provider} has no default judge model; pass --judge-model" in capsys.readouterr().err
    args = parse_args([*COMMANDS["subjects run"], "--judge-provider", provider, "--judge-model", "m"])
    assert args.judge_model == "m"


def test_the_placeholder_reads_as_the_default_judge_without_parse_args():
    assert JUDGE_MODEL_FOR_PROVIDER == DEFAULT_JUDGE_MODEL
    assert default_judge_model("openai") == DEFAULT_GENERATOR_MODEL
    assert default_judge_model("google") is None


def test_the_legacy_cli_shim_exports_the_resolving_parse_args():
    import importlib.util
    from pathlib import Path

    shim_path = Path(__file__).resolve().parents[1] / "cli.py"
    spec = importlib.util.spec_from_file_location("dokimi_legacy_cli_shim", shim_path)
    assert spec is not None and spec.loader is not None
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    assert "parse_args" in shim.__all__
    assert shim.parse_args is parse_args
    args = shim.parse_args([*COMMANDS["eval score"], "--judge-provider", "openai"])
    assert args.judge_model == DEFAULT_GENERATOR_MODEL
