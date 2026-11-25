# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The arms, baseline and harness moves (issue #94 §2.4, F1b).

Old spellings tombstone with the new argv; the new spellings parse to the same value, and a
repeated flag joins into the comma list the old flag took.
"""

from __future__ import annotations

import pytest

from dokimi import cli, cli_routes


def _tombstone(argv: list[str], capsys) -> str:
    """The text of the tombstone that ``argv`` prints; it must exit 2 without running anything."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize(("old", "new"), [
    (["arms", "run", "--product", "p", "--questions", "q", "--arms", "baseline,docs"],
     ["arms", "run", "--product", "p", "--questions", "q", "--arm", "baseline,docs"]),
    (["arms", "matrix-run", "--product", "p", "--questions", "q", "--matrix-cells", "c.json", "--arms", "baseline"],
     ["arms", "matrix-run", "--product", "p", "--questions", "q", "--matrix-cells", "c.json", "--arm", "baseline"]),
    (["explore", "run", "--all", "--arms", "oracle,empty"], ["explore", "run", "--all", "--arm", "oracle,empty"]),
    (["tasks", "run", "--all", "--harnesses", "codex,claude-code"],
     ["tasks", "run", "--all", "--harness", "codex,claude-code"]),
    (["arms", "plugin-delta", "--baseline-json", "b.json", "--plugin-json", "p.json"],
     ["arms", "plugin-delta", "--baseline", "b.json", "--plugin-json", "p.json"]),
    (["docs", "compare", "--base", "a.json", "--candidate", "b.json"],
     ["docs", "compare", "--baseline", "a.json", "--candidate", "b.json"]),
])
def test_an_old_spelling_tombstones_with_the_new_argv(old, new, capsys):
    text = _tombstone(old, capsys)
    assert " ".join(new) in text


def test_the_moved_old_flag_is_not_accepted_by_the_parser(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["arms", "run", "--product", "p", "--questions", "q", "--arm", "x", "--arms", "baseline"])
    assert "unrecognized arguments: --arms baseline" in capsys.readouterr().err


@pytest.mark.parametrize(("argv", "dest", "value"), [
    (["arms", "run", "--product", "p", "--questions", "q", "--arm", "baseline,docs"], "arms", "baseline,docs"),
    (["arms", "run", "--product", "p", "--questions", "q", "--arm", "baseline", "--arm", "docs"], "arms", "baseline,docs"),
    (["arms", "matrix-run", "--product", "p", "--questions", "q", "--matrix-cells", "c.json", "--arm", "a", "--arm", "b,c"], "arms", "a,b,c"),
    (["explore", "run", "--all", "--arm", "oracle", "--arm", "empty"], "arms", "oracle,empty"),
    (["tasks", "run", "--all", "--harness", "codex", "--harness", "claude-code"], "harnesses", "codex,claude-code"),
    (["arms", "plugin-delta", "--baseline", "b.json", "--plugin-json", "p.json"], "baseline_json", "b.json"),
    (["docs", "compare", "--baseline", "a.json", "--candidate", "b.json"], "base", "a.json"),
])
def test_the_new_spellings_parse_to_the_value_the_old_ones_gave(argv, dest, value):
    assert getattr(cli.build_parser().parse_args(argv), dest) == value


def test_a_repeated_arm_is_the_comma_list_the_old_flag_took():
    old = cli.build_parser().parse_args(["arms", "run", "--product", "p", "--questions", "q", "--arm", "baseline,docs,skill:s"])
    new = cli.build_parser().parse_args(["arms", "run", "--product", "p", "--questions", "q", "--arm", "baseline",
                                         "--arm", "docs", "--arm", "skill:s"])
    assert old.arms == new.arms == "baseline,docs,skill:s"


def test_the_cells_and_plugin_json_spellings_that_stay_are_not_moved():
    assert cli_routes.moved_flags_argv(["arms", "run", "--matrix-cells", "c.json"]) is None
    assert cli_routes.moved_flags_argv(["arms", "plugin-delta", "--plugin-json", "p.json"]) is None
    assert cli_routes.moved_flags_argv(["arms", "run", "--arm", "baseline"]) is None
