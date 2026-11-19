# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The spend and dry-run concepts' moves (issue #94 §2.4, F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import pytest

from dokimi import cli, cli_flags, cli_routes
from dokimi.cli import build_parser


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), [
    # spend: a whole-command cap is --max-usd, a cap per agent run is --max-usd-per-cell
    (["tools", "preference", "--groups", "g.json", "--max-cost-usd", "5"],
     ["tools", "preference", "--groups", "g.json", "--max-usd", "5"]),
    (["repo", "run", "src", "--max-batch-usd", "5"], ["repo", "run", "src", "--max-usd", "5"]),
    (["repo", "run", "src", "--budget=0.5"], ["repo", "run", "src", "--max-usd-per-cell=0.5"]),
    (["repo", "reach", "src", "--budget", "0.1"], ["repo", "reach", "src", "--max-usd-per-cell", "0.1"]),
    (["repo", "skill-reach", "--budget", "0.1"], ["repo", "skill-reach", "--max-usd-per-cell", "0.1"]),
    # dry-run: an estimate, no paid call
    (["catalog", "routing", "--estimate-only"], ["catalog", "routing", "--dry-run"]),
    (["skill", "routing", "--estimate-only"], ["skill", "routing", "--dry-run"]),
    (["skill", "novelty", "analyze", "--artifact", "skill:x", "--estimate-only"],
     ["skill", "novelty", "analyze", "--artifact", "skill:x", "--dry-run"]),
])
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join(new) in text
    assert cli_flags.registry_problems() == []


@pytest.mark.parametrize("argv", [
    ["tools", "preference", "--groups", "g.json", "--max-usd", "5"],
    ["repo", "run", "src", "--max-usd-per-cell", "2"],
    ["repo", "run", "src", "--max-usd", "5", "--budget-ledger", "l.json"],  # a longer flag is not the old one
    ["subjects", "run", "--dry-run"],
    ["skill", "novelty", "analyze", "--artifact", "skill:x", "--dry-run"],
    ["repo", "run", "--", "--budget", "1"],  # after `--` every token is a positional, not a flag
    ["catalog", "granularity", "--budget", "4"],  # `--budget` keeps no meaning here, but only the listed leaves move
])
def test_a_canonical_spelling_is_not_moved(argv):
    assert cli_routes.moved_flags_argv(argv) is None


def test_the_budget_move_applies_only_to_the_per_agent_run_leaves():
    # `--budget` named the cap per agent run on these leaves and nowhere else.
    assert cli_routes.moved_flags_argv(["repo", "run", "src", "--budget", "1"]) == [
        "repo", "run", "src", "--max-usd-per-cell", "1"]
    assert cli_routes.moved_flags_argv(["catalog", "granularity", "--budget", "4"]) is None


def test_a_moved_spend_flag_parses_to_the_same_value_as_its_new_spelling():
    old = ["tools", "preference", "--groups", "g.json", "--max-cost-usd", "0.5"]
    new = cli_routes.moved_flags_argv(old)
    assert new is not None
    assert build_parser().parse_args(new).max_usd == 0.5


def test_a_moved_dry_run_flag_parses_to_the_same_destination_as_its_new_spelling():
    old = ["skill", "novelty", "analyze", "--artifact", "skill:x", "--estimate-only"]
    new = cli_routes.moved_flags_argv(old)
    assert new is not None
    assert build_parser().parse_args(new).estimate_only is True
    assert build_parser().parse_args(new[:-1]).estimate_only is False
