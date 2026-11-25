# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The network, timeout and cache concepts' moves (issue #94 §2.4, F1b): old spellings tombstone, new ones parse the same."""

from __future__ import annotations

import pytest

from dokimi import cli, cli_routes
from dokimi.cli import build_parser
from dokimi.harnesses import network_policy


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), [
    # timeout: the phase is a prefix of the value
    (["repo", "run", "src", "--build-timeout", "5"], ["repo", "run", "src", "--timeout", "build=5"]),
    (["repo", "run", "src", "--grade-timeout=600"], ["repo", "run", "src", "--timeout=verify=600"]),
    (["eval", "check", "src", "--build-timeout", "36000"], ["eval", "check", "src", "--timeout", "build=36000"]),
    (["eval", "check", "src", "--grade-timeout", "600"], ["eval", "check", "src", "--timeout", "verify=600"]),
])
def test_an_old_timeout_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert text.startswith("moved: dokimi ")
    assert " ".join(new) in text


@pytest.mark.parametrize("argv", [
    ["repo", "run", "src", "--timeout", "900"],  # a bare SEC is the agent run's
    ["repo", "run", "src", "--timeout", "build=5", "--timeout", "verify=60"],
    ["eval", "check", "src", "--timeout", "build=5"],
    ["tasks", "run", "--all", "--harnesses", "h", "--network", "none"],
])
def test_a_canonical_timeout_or_network_spelling_is_not_moved(argv):
    assert cli_routes.moved_flags_argv(argv) is None


REPO_RUN = ["repo", "run", "src", "--arm", "guidance:none", "--model", "m", "--batch", "b"]


@pytest.mark.parametrize(("argv", "dest", "value"), [
    ([*REPO_RUN, "--timeout", "900"], "timeout", 900),
    ([*REPO_RUN, "--timeout", "build=5"], "build_timeout", 5),
    ([*REPO_RUN, "--timeout", "verify=60"], "grade_timeout", 60),
    (["eval", "check", "src", "--timeout", "build=36000"], "build_timeout", 36000),
    (["eval", "check", "src", "--timeout", "verify=600"], "grade_timeout", 600),
])
def test_a_timeout_phase_sets_the_dest_the_phase_always_set(argv, dest, value):
    assert getattr(build_parser().parse_args(argv), dest) == value


def test_a_bare_seconds_value_is_refused_on_eval_check():
    """eval check has no solve phase: a bare SEC (the agent run) is refused, not read as some other phase."""
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["eval", "check", "src", "--timeout", "900"])
    assert exc.value.code == 2


def test_an_unknown_timeout_phase_is_refused(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([*REPO_RUN, "--timeout", "bogus=5"])
    assert exc.value.code == 2
    assert "'bogus' is not a phase of this command" in capsys.readouterr().err


def test_a_moved_timeout_value_after_a_flag_is_still_prefixed():
    """The value is the next token even when the token before the old flag is a flag."""
    argv = ["eval", "check", "--schema-only", "--build-timeout", "5", "src"]
    assert cli_routes.moved_flags_argv(argv) == ["eval", "check", "--schema-only", "--timeout", "build=5", "src"]


def test_the_timeout_defaults_are_the_documented_seconds():
    args = build_parser().parse_args(REPO_RUN)
    assert (args.timeout, args.grade_timeout) == (2700, 3600)


def test_a_moved_timeout_flag_with_no_value_does_not_index_past_the_end():
    """The value-aware rewrite reads the next token only when there is one."""
    assert cli_routes.moved_flags_argv(["eval", "check", "src", "--build-timeout"]) == ["eval", "check", "src", "--timeout"]


def test_a_bare_network_mode_means_every_phase():
    """`tasks run --network none` (no PHASE=) sets every phase, as the registry's spelling promises."""
    args = build_parser().parse_args(["tasks", "run", "--all", "--harnesses", "h", "--network", "none"])
    overrides = network_policy.parse_override(args.network[0])
    assert set(overrides) == set(network_policy.PHASES)
    assert {p.mode for p in overrides.values()} == {"none"}
