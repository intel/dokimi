# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``--judge-provider`` without ``--judge-model`` gets a judge model that runs there."""

from __future__ import annotations

import argparse
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser, parse_args
from dokimi.defaults import (
    DEFAULT_GENERATOR_MODEL,
    DEFAULT_JUDGE_MODEL,
    JUDGE_MODEL_FOR_PROVIDER,
)
from dokimi.eval.arms_run import ArmsRunConfig, cell_config, refuse_deferred_self_judge
from dokimi.judge_rule import JudgeIsAnswerer, apply_default_judge, default_judge


def _judge_commands() -> dict[str, list[str]]:
    """Every subcommand with ``--judge-provider`` but the novelty ones -> argv with its required args."""
    found: dict[str, list[str]] = {}

    def walk(parser: argparse.ArgumentParser, path: list[str]) -> None:
        dests = {a.dest for a in parser._actions}
        if "judge_provider" in dests and path[0] != "novelty" and path[:2] != ["skill", "novelty"]:
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
#: The commands that answer a question (have ``--model``); the ``eval score``/``eval judge`` ones only grade.
ANSWERED_COMMANDS = {"arms run", "arms matrix-run", "benchmark run", "benchmark preflight",
                     "benchmark batch", "evaluate", "subjects run"}


def test_the_commands_that_take_a_judge_provider_are_all_covered():
    assert set(COMMANDS) == {
        "evaluate", "eval score pointwise", "eval judge validate", "eval score checklist",
        "eval judge wording", "eval score pairwise", "benchmark run", "benchmark preflight",
        "benchmark batch", "arms run", "arms matrix-run", "subjects run", "eval score reference",
    }


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_judge_command_defaults_the_model_to_its_provider(command):
    argv = COMMANDS[command]
    assert build_parser().parse_args(argv).judge_model is JUDGE_MODEL_FOR_PROVIDER
    # The answerer is the default gpt-6.1-sol; a command with no answerer (eval score/judge) judges it.
    if command in ANSWERED_COMMANDS:
        argv = [*argv, "--model", DEFAULT_JUDGE_MODEL]
    args = parse_args([*argv, "--judge-provider", "openai"])
    if command == "subjects run":  # resolved once the descriptor is loaded, see the test below
        apply_default_judge(args, matrix=False, named=True)
    assert args.judge_model == DEFAULT_GENERATOR_MODEL


@pytest.mark.parametrize("provider, model", [
    ("anthropic", DEFAULT_JUDGE_MODEL),
    ("amazon-bedrock", DEFAULT_JUDGE_MODEL),
])
def test_a_provider_alone_takes_the_default_judge_that_runs_there(provider, model):
    args = parse_args([*COMMANDS["eval score pointwise"], "--judge-provider", provider])
    assert (args.judge_provider, args.judge_model) == (provider, model)
    assert type(args.judge_model) is str


def test_a_provider_alone_whose_default_judge_is_the_answerer_exits(capsys):
    # The default answerer gpt-6.1-sol is the only default on openai: the shared rule refuses it.
    with pytest.raises(SystemExit) as exc:
        parse_args([*COMMANDS["benchmark run"], "--judge-provider", "openai"])
    assert exc.value.code == 2
    assert "--judge-provider openai: the default judge there is the model under measurement" in capsys.readouterr().err


def test_a_claude_cli_judge_is_an_alias_of_another_family():
    args = parse_args([*COMMANDS["arms run"], "--judge-provider", "claude-cli"])
    assert (args.judge_provider, args.judge_model) == ("claude-cli", "opus")


def test_a_library_judge_left_out_is_the_provider_default_and_is_refused_only_at_run_start():
    # Construction resolves the default judge as base did (the matrix shape: no model, so the default answerer).
    config = ArmsRunConfig(product="demo", judge=True, judge_provider="openai")
    assert (config.judge_provider, config.judge_model) == ("openai", "gpt-6.1-sol")
    with pytest.raises(ValueError, match="pass --judge-model as well"):
        refuse_deferred_self_judge(config)
    # A matrix cell of another model judges with the same default judge, as on base.
    cell = replace(config, model="claude-sonnet-5-5", provider="anthropic")
    refuse_deferred_self_judge(cell)
    assert cell.judge_model == "gpt-6.1-sol"


def test_a_library_claude_cli_answerer_judges_on_its_cli_like_the_cli_does():
    config = ArmsRunConfig(product="demo", model="sonnet", provider="claude-cli", judge=True)
    assert (config.judge_provider, config.judge_model) == ("claude-cli", "opus")
    refuse_deferred_self_judge(config)


def test_a_library_judge_provider_named_is_kept_for_a_claude_cli_answerer():
    # An explicit anthropic judge is not the field default: it is kept, as on base.
    config = ArmsRunConfig(product="demo", model="opus", provider="claude-cli", judge=True, judge_provider="anthropic")
    assert (config.judge_provider, config.judge_model) == ("anthropic", "claude-sonnet-5-5")


def test_a_matrix_cell_whose_model_is_the_default_judge_is_warned_not_refused():
    config = ArmsRunConfig(product="demo", judge=True, judge_provider="openai")
    cell = replace(config, matrix_cell="gpt", model="gpt-6.1-sol", provider="openai")
    refuse_deferred_self_judge(cell)  # warn-only: no raise
    assert cell.judge_model == "gpt-6.1-sol"


def test_a_matrix_claude_cli_cell_keeps_the_matrix_judge_provider():
    # cell_config copies the config with the cell's provider: a claude-cli cell must not move the shared judge.
    config = ArmsRunConfig(product="demo", judge=True, model="gpt-6.1-sol", provider="openai")
    assert config.judge_provider == "anthropic"
    cell = SimpleNamespace(name="cli", model="sonnet", provider="claude-cli", harness="arms-runner", plugin_specs=())
    copied = cell_config(config, cell)
    assert (copied.judge_provider, copied.judge_model) == (config.judge_provider, config.judge_model)
    # A sampled matrix still has one API judge: a CLI judge would refuse the temperature.
    sampled = cell_config(replace(config, judge_temperature=0.5), cell)
    assert sampled.judge_provider == "anthropic"


def test_a_claude_cli_answerer_judges_on_its_cli_when_no_judge_provider_is_named():
    # The CLI's argparse default (anthropic) must not stop the rule from taking the answerer's provider.
    args = parse_args([*COMMANDS["arms run"], "--provider", "claude-cli", "--model", "sonnet", "--judge"])
    assert (args.judge_provider, args.judge_model) == ("claude-cli", "opus")


@pytest.mark.parametrize("answerer, provider, judge", [
    ("claude-sonnet-5-5", "anthropic", None),  # no other Anthropic default: refused
    ("claude-sonnet-5-5", "openai", DEFAULT_GENERATOR_MODEL),
    ("gpt-6.1-sol", "claude-cli", "opus"),
])
def test_arms_run_judge_is_not_the_answerer(answerer, provider, judge):
    if judge is None:
        # Construction does not refuse; the run start does (refuse_deferred_self_judge).
        config = ArmsRunConfig(product="demo", model=answerer, provider="openai", judge_provider=provider, judge=True)
        with pytest.raises(ValueError, match="pass --judge-model as well"):
            refuse_deferred_self_judge(config)
        # Not judging, the conflict is not a refusal: nothing reads the judge.
        assert ArmsRunConfig(product="demo", model=answerer, provider="openai", judge_provider=provider).judge is False
    else:
        config = ArmsRunConfig(product="demo", model=answerer, provider="openai", judge_provider=provider, judge=True)
        assert config.judge_model == judge


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
    args = parse_args([*COMMANDS["eval score pointwise"], "--judge-provider", "openai",
                       "--judge-model", DEFAULT_JUDGE_MODEL])
    assert args.judge_model == DEFAULT_JUDGE_MODEL


@pytest.mark.parametrize("provider", ["google", "google-vertex", "openrouter"])
def test_a_provider_with_no_default_judge_exits_before_running(provider, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*COMMANDS["arms run"], "--judge-provider", provider])
    assert exc.value.code == 2
    assert f"--judge-provider {provider} has no default judge model; pass --judge-model" in capsys.readouterr().err
    # subjects run takes its judge once the descriptor is loaded, and refuses it there.
    args = parse_args([*COMMANDS["subjects run"], "--judge-provider", provider])
    with pytest.raises(ValueError, match=f"--judge-provider {provider} has no default judge model"):
        apply_default_judge(args, matrix=False, named=True)
    args = parse_args([*COMMANDS["subjects run"], "--judge-provider", provider, "--judge-model", "m"])
    assert args.judge_model == "m"


def test_the_placeholder_reads_as_the_default_judge_without_parse_args():
    assert JUDGE_MODEL_FOR_PROVIDER == DEFAULT_JUDGE_MODEL
    assert default_judge(None, "openai") == (DEFAULT_GENERATOR_MODEL, "openai")
    with pytest.raises(ValueError, match="has no default judge model"):
        default_judge(None, "google")


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
    args = shim.parse_args([*COMMANDS["eval score pointwise"], "--judge-provider", "openai"])
    assert args.judge_model == DEFAULT_GENERATOR_MODEL


def test_preflight_refuses_the_judge_that_run_refuses(capsys):
    # preflight shows the plan the run will take: the same flags that `benchmark run` refuses are refused here
    with pytest.raises(SystemExit) as exc:
        parse_args(["benchmark", "preflight", "--library", "onetbb", "--judge-provider", "openai"])
    assert exc.value.code == 2
    assert "the default judge there is the model under measurement" in capsys.readouterr().err


def test_a_matrix_run_takes_no_top_level_model_as_its_answerer(tmp_path):
    # Every matrix cell sets its own model, so the default top-level --model is not the answerer.
    args = parse_args(["arms", "matrix-run", "--product", "p", "--questions", "q.jsonl",
                       "--arms", "a.json", "--matrix-cells", str(tmp_path / "cells.json"),
                       "--judge", "--judge-provider", "openai"])
    assert args.judge_model == DEFAULT_GENERATOR_MODEL


def test_subjects_run_judge_provider_is_not_refused_before_the_descriptor_loads():
    # The top-level --model of subjects run is no answerer when the descriptor has explicit cells, so the rule waits.
    args = parse_args([*COMMANDS["subjects run"], "--judge", "--judge-provider", "openai"])
    assert args.judge_model is JUDGE_MODEL_FOR_PROVIDER
    apply_default_judge(args, matrix=True, named=True)
    assert args.judge_model == DEFAULT_GENERATOR_MODEL
    # Implicit cells: the top-level model is the answerer, and it is still refused.
    args = parse_args([*COMMANDS["subjects run"], "--judge", "--judge-provider", "openai"])
    with pytest.raises(JudgeIsAnswerer):
        apply_default_judge(args, matrix=False, named=True)


def test_the_judge_model_help_names_the_claude_cli_judge_the_rule_picks():
    # the help said claude-sonnet-5-5 (an alias) was the claude-cli default; the rule picks opus there
    from dokimi.defaults import JUDGE_MODEL_HELP

    assert default_judge("gpt-6.1-sol", "claude-cli") == ("opus", "claude-cli")
    assert "opus on claude-cli (sonnet when the answerer is opus)" in JUDGE_MODEL_HELP
    assert "claude-cli (an alias)" not in JUDGE_MODEL_HELP


def test_a_library_refused_default_self_judge_stops_before_the_probe_or_the_library_lookup(monkeypatch):
    # the refusal reads only the config, so it comes before the network-backed steps of prepare_arms
    from dokimi.eval import arms_run

    calls: list[str] = []
    monkeypatch.setattr(arms_run, "_treatments", lambda *a, **k: calls.append("treatments"))
    monkeypatch.setattr(arms_run, "check_sampling_support", lambda config: calls.append("probe"))
    config = ArmsRunConfig(product="demo", model="claude-sonnet-5-5", provider="anthropic", judge=True)
    with pytest.raises(JudgeIsAnswerer):
        arms_run.prepare_arms(config, ["baseline"], [])
    assert calls == []


def test_an_explicit_alias_judge_model_on_a_claude_cli_answerer_judges_on_its_cli():
    # the left-out judge model follows the CLI answerer onto claude-cli; an explicit alias must too, or the run
    # sends `haiku` to the Anthropic API and fails at the first judge call, after the answers are paid for
    args = parse_args([*COMMANDS["arms run"], "--provider", "claude-cli", "--model", "sonnet", "--judge",
                       "--judge-model", "haiku"])
    assert (args.judge_provider, args.judge_model) == ("claude-cli", "haiku")
    config = ArmsRunConfig(product="demo", model="sonnet", provider="claude-cli", judge=True, judge_model="haiku")
    assert (config.judge_provider, config.judge_model) == ("claude-cli", "haiku")


def test_a_named_judge_provider_is_kept_for_an_explicit_judge_model_on_a_claude_cli_answerer():
    args = parse_args([*COMMANDS["arms run"], "--provider", "claude-cli", "--model", "sonnet", "--judge",
                       "--judge-model", "haiku", "--judge-provider", "anthropic"])
    assert (args.judge_provider, args.judge_model) == ("anthropic", "haiku")


def test_subjects_run_does_not_move_the_judge_to_a_cli_answerer_before_the_descriptor_loads():
    from dokimi.commands.subjects import resolve_judge

    # parse_args leaves the judge to the descriptor, which picks it once loaded (resolve_judge).
    # With or without an explicit --judge-model.
    for extra in ([], ["--judge-model", "haiku"]):
        args = parse_args([*COMMANDS["subjects run"], "--judge", "--provider", "claude-cli", "--model", "sonnet",
                           *extra])
        assert args.judge_provider == "anthropic"
    # Implicit cells: the top-level CLI answerer is the answerer, so the left-out judge follows it onto its CLI.
    args = parse_args([*COMMANDS["subjects run"], "--judge", "--provider", "claude-cli", "--model", "sonnet"])
    resolve_judge(args, SimpleNamespace(matrix_cells_explicit=False))
    assert (args.judge_provider, args.judge_model) == ("claude-cli", "opus")


@pytest.mark.parametrize(("explicit_cells", "expected"), [
    (False, ("claude-cli", "haiku")),  # implicit cells: an explicit alias follows the CLI answerer onto its CLI
    (True, ("anthropic", "haiku")),    # explicit cells: the descriptor's cells are the answerers, the judge stays
])
def test_subjects_run_resolves_an_explicit_judge_model_once_the_descriptor_is_loaded(explicit_cells, expected):
    from dokimi.commands.subjects import resolve_judge

    args = parse_args([*COMMANDS["subjects run"], "--judge", "--provider", "claude-cli", "--model", "sonnet",
                       "--judge-model", "haiku"])
    resolve_judge(args, SimpleNamespace(matrix_cells_explicit=explicit_cells))
    assert (args.judge_provider, args.judge_model) == expected


def test_subjects_run_refuses_a_judge_model_the_cli_judge_it_moves_to_cannot_take():
    # parse_args checks the model before the descriptor moves the judge; the move is checked again here
    from dokimi.commands.subjects import resolve_judge
    from dokimi.utils import InputError

    args = parse_args([*COMMANDS["subjects run"], "--judge", "--provider", "claude-cli", "--model", "sonnet",
                       "--judge-model", "gpt-4o"])
    with pytest.raises(InputError, match="takes a Claude model"):
        resolve_judge(args, SimpleNamespace(matrix_cells_explicit=False))


def test_subjects_run_refuses_a_default_judge_that_is_the_implicit_cell_answerer():
    from dokimi.commands.subjects import resolve_judge
    from dokimi.utils import InputError

    argv = [*COMMANDS["subjects run"], "--judge", "--model", "claude-sonnet-5-5"]
    with pytest.raises(InputError, match="model under measurement"):
        resolve_judge(parse_args(argv), SimpleNamespace(matrix_cells_explicit=False))
    # Explicit cells are the answerers, so the top-level --model excludes nothing.
    args = parse_args(argv)
    resolve_judge(args, SimpleNamespace(matrix_cells_explicit=True))
    assert (args.judge_provider, args.judge_model) == ("anthropic", "claude-sonnet-5-5")
