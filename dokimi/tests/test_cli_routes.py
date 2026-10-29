# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The CLI route table: sections, cost labels, tombstones, and ``data schema``."""

from __future__ import annotations

import argparse
import json
import re

import pytest

from dokimi import cli_routes
from dokimi.cli import build_parser, parse_args

SECTION_HEADING = re.compile(r"^(%s):$" % "|".join(re.escape(s) for s in cli_routes.SECTIONS))


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _leaf_paths(
    parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    """Paths of every parser that has no subcommands, aliases resolved to their parser once."""
    found: set[tuple[str, ...]] = set()
    sub = _subparsers(parser)
    if sub is None:
        return {prefix} if prefix else set()
    seen: set[int] = set()
    for name, child in sub.choices.items():
        if id(child) in seen:
            continue
        seen.add(id(child))
        found |= _leaf_paths(child, (*prefix, name))
    return found


def _help_actions(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    """(path, pseudo action) for every subcommand that has a help line, at every depth."""
    sub = _subparsers(parser)
    if sub is None:
        return
    for action in sub._choices_actions:
        yield (*prefix, action.dest), action
    seen: set[int] = set()
    for name, child in sub.choices.items():
        if id(child) in seen:  # an alias
            continue
        seen.add(id(child))
        yield from _help_actions(child, (*prefix, name))


def test_help_lists_each_root_under_its_section():
    text = build_parser().format_help()
    listed: dict[str, str] = {}
    section = None
    order: list[str] = []
    for line in text.splitlines():
        if SECTION_HEADING.match(line):
            section = line[:-1]
            order.append(section)
        elif line == "options:":
            section = None
        elif section and re.match(r"^  \S", line):
            listed[line.split()[0]] = section
    assert order == list(cli_routes.SECTIONS)
    assert listed == {root.name: root.section for root in cli_routes.ROOTS}


def test_every_route_help_line_ends_in_its_cost_label():
    top = build_parser()
    checked = 0
    for path, action in _help_actions(top):
        cost = cli_routes.cost_of(path)
        assert cost in ("free", "paid"), path
        assert action.help and action.help.endswith(f" [{cost}]"), (path, action.help)
        checked += 1
    assert checked >= len(cli_routes.LEAVES)


def test_run_and_compare_have_a_one_line_help_in_the_top_level_list():
    text = build_parser().format_help()
    for name in ("run", "compare"):
        line = next(line for line in text.splitlines() if re.match(rf"^  {name}\s", line))
        assert re.search(r"\S.* \[(free|paid)\]$", line), line


def test_route_table_matches_the_parser_leaves_and_roots():
    top = build_parser()
    assert _leaf_paths(top) == {leaf.path for leaf in cli_routes.LEAVES}
    sub = _subparsers(top)
    assert sub is not None
    names = set(sub.choices)
    aliases = {alias for root in cli_routes.ROOTS for alias in root.aliases}
    assert names - aliases == {root.name for root in cli_routes.ROOTS}
    assert aliases == {"product"}


def test_a_root_costs_paid_when_any_of_its_leaves_is_paid():
    assert cli_routes.cost_of(("repo",)) == "paid"
    assert cli_routes.cost_of(("library",)) == "free"
    assert cli_routes.cost_of(("data",)) == "free"
    assert cli_routes.cost_of(("eval", "judge-validity")) == "paid"
    assert cli_routes.cost_of(("no", "such", "route")) is None


def test_the_tombstone_table_is_empty_and_its_check_finds_a_live_route():
    assert cli_routes.TOMBSTONES == ()
    assert cli_routes.tombstone_problems() == []
    bad = (cli_routes.Tombstone(("eval", "score"), ("eval", "inspect")),)
    assert cli_routes.tombstone_problems(bad)  # the old path is still a live route


def test_a_tombstone_rewrites_the_argv_and_exits_2(monkeypatch, capsys):
    # Test-only entry: no command moves in this release.
    monkeypatch.setattr(
        cli_routes,
        "TOMBSTONES",
        (cli_routes.Tombstone(("old-tool", "inspect"), ("eval", "inspect")),),
    )
    with pytest.raises(SystemExit) as exc:
        parse_args(["old-tool", "inspect", "data.json"])
    assert exc.value.code == 2
    assert "moved: dokimi eval inspect data.json" in capsys.readouterr().err


def test_an_argv_that_no_tombstone_matches_is_parsed_as_before():
    args = parse_args(["data", "schema", "list"])
    assert args.cmd == "data"


def test_data_schema_show_prints_the_route_table_as_json(capsys):
    args = parse_args(["data", "schema", "show", "cli_routes"])
    args.func(args)
    doc = json.loads(capsys.readouterr().out)
    assert doc == cli_routes.document()
    assert doc["schema_version"] == "cli_routes.v1"
    assert {"path": ["eval", "score"], "cost": "paid"} in doc["leaves"]


def test_data_schema_list_names_the_kinds(capsys):
    args = parse_args(["data", "schema", "list"])
    args.func(args)
    assert capsys.readouterr().out.split() == ["cli_routes"]
