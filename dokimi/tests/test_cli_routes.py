# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The CLI route table: sections, cost labels, tombstones, and ``data schema``."""

from __future__ import annotations

import argparse
import json
import re

import pytest

from dokimi import cli_routes
from dokimi.artifacts import schema_errors
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
    loop = "The loop"
    subjects = "Subjects"
    evals = "Evals"
    data = "Analysis and data"
    assert listed == {
        **dict.fromkeys(
            (
                "evaluate",
                "baseline",
                "report",
                "answers",
                "benchmark",
                "dashboard",
                "arms",
                "subjects",
                "tasks",
                "explore",
            ),
            loop,
        ),
        **dict.fromkeys(("run", "compare", "library", "catalog", "novelty", "repo"), subjects),
        **dict.fromkeys(("personas", "questions", "eval"), evals),
        **dict.fromkeys(("dataset", "ingest", "tools", "context", "runs", "backends", "data"), data),
    }


# The commands that can make a model call. Written out here, not read from cli_routes, so a
# label that says `free` on a command that spends money (or the reverse) fails a test.
PAID_PATHS = {
    ("evaluate",),
    ("answers", "generate"),
    ("benchmark", "run"),
    ("benchmark", "batch"),
    ("arms", "run"),
    ("arms", "matrix-run"),
    ("subjects", "run"),
    ("tasks", "run"),
    ("explore", "run"),
    ("catalog", "trigger-set"),
    ("catalog", "trigger-probe"),
    ("novelty", "analyze"),
    ("novelty", "atoms"),
    ("novelty", "probe"),
    ("novelty", "shorten"),
    ("novelty", "routing"),
    ("repo", "reach"),
    ("repo", "run"),
    ("repo", "hits", "judge"),
    ("personas", "discover"),
    ("questions", "generate"),
    ("questions", "analyze"),
    ("questions", "refine"),
    ("questions", "panel-review"),
    ("eval", "score"),
    ("eval", "ragas"),
    ("eval", "judge-validity"),
    ("eval", "checklist"),
    ("eval", "judge-wording"),
    ("eval", "cross-judge"),
    ("eval", "pairwise"),
    ("eval", "reference"),
    ("eval", "panel-score"),
    ("tools", "preference"),
}


def test_the_paid_leaves_are_exactly_the_ones_written_out_here():
    assert {leaf.path for leaf in cli_routes.LEAVES if leaf.cost == "paid"} == PAID_PATHS


def test_the_help_label_of_a_route_comes_from_its_literal_cost():
    top = build_parser()
    for path, action in _help_actions(top):
        paid = any(p[: len(path)] == path for p in PAID_PATHS)
        assert action.help.endswith(" [paid]" if paid else " [free]"), (path, action.help)


def test_every_route_help_line_ends_in_its_cost_label():
    top = build_parser()
    seen = set()
    for path, action in _help_actions(top):
        cost = cli_routes.cost_of(path)
        assert cost in ("free", "paid"), path
        assert action.help and action.help.endswith(f" [{cost}]"), (path, action.help)
        seen.add(path)
    # A leaf with no help= is not in _choices_actions, so it is named, not counted: a count
    # would include the root and group lines. `run` and `compare`: see the test below.
    assert {leaf.path for leaf in cli_routes.LEAVES if leaf.summary is None} <= seen


def test_run_and_compare_have_a_one_line_help_in_the_top_level_list(monkeypatch):
    # argparse wraps help at COLUMNS (or the terminal width, else 80): a narrow host would
    # wrap the cost label onto a continuation line, so pin the width the label is read at.
    monkeypatch.setenv("COLUMNS", "200")
    text = build_parser().format_help()
    expected = {
        "run": "Run the static benchmark spec and save a snapshot [free]",
        "compare": "Compare two saved benchmark snapshots [free]",
    }
    for name, help_line in expected.items():
        line = next(line for line in text.splitlines() if re.match(rf"^  {name}\s", line))
        assert line.split(None, 1)[1] == help_line, line


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


def test_the_tombstone_check_follows_groups_aliases_and_roots():
    # moved_argv matches by prefix: a group tombstone shadows the live routes under it.
    shadows = (cli_routes.Tombstone(("repo", "tasks"), ("tasks", "run")),)
    assert cli_routes.tombstone_problems(shadows)
    # an alias is a live route too
    alias = (cli_routes.Tombstone(("product",), ("library", "list")),)
    assert cli_routes.tombstone_problems(alias)
    # a root is a valid destination, and an unknown name is a valid old path
    root = (cli_routes.Tombstone(("old-tool",), ("library",)),)
    assert cli_routes.tombstone_problems(root) == []
    nowhere = (cli_routes.Tombstone(("old-tool",), ("no-such-root",)),)
    assert cli_routes.tombstone_problems(nowhere)
    # compare takes positional files, so "compare x" is a live invocation, not a free old path
    extends = (cli_routes.Tombstone(("compare", "x"), ("compare",)),)
    assert cli_routes.tombstone_problems(extends)
    # an empty old path matches every argv; an option is not a command
    assert cli_routes.tombstone_problems((cli_routes.Tombstone((), ("data",)),))
    assert cli_routes.tombstone_problems((cli_routes.Tombstone(("--version",), ("data",)),))


def test_a_tombstone_matches_an_alias_typed_on_the_command_line():
    tombs = (cli_routes.Tombstone(("library", "old-intents"), ("library", "intents")),)
    assert cli_routes.moved_argv(["product", "old-intents"], tombs) == ["library", "intents"]
    assert cli_routes.moved_argv(["library", "old-intents", "x"], tombs) == [
        "library",
        "intents",
        "x",
    ]
    assert cli_routes.moved_argv(["library", "list"], tombs) is None


def test_a_tombstone_keeps_the_arguments_after_the_old_path_whatever_the_lengths():
    longer_old = (cli_routes.Tombstone(("product", "old-intents", "all"), ("library", "intents")),)
    assert cli_routes.moved_argv(["product", "old-intents", "all", "x"], longer_old) == [
        "library",
        "intents",
        "x",
    ]
    longer_new = (cli_routes.Tombstone(("product", "old"), ("library", "show", "intents")),)
    assert cli_routes.moved_argv(["product", "old", "x"], longer_new) == [
        "library",
        "show",
        "intents",
        "x",
    ]


def test_an_empty_argv_matches_no_tombstone():
    tombs = (cli_routes.Tombstone(("old-tool",), ("library",)),)
    assert cli_routes.moved_argv([], tombs) is None
    with pytest.raises(SystemExit) as exc:  # argparse still asks for a command
        parse_args([])
    assert exc.value.code == 2


def test_every_alias_points_at_its_own_root_parser():
    sub = _subparsers(build_parser())
    assert sub is not None
    for root in cli_routes.ROOTS:
        for alias in root.aliases:
            assert sub.choices[alias] is sub.choices[root.name], (alias, root.name)


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


def test_the_route_document_reports_each_roots_section_aliases_and_cost():
    doc = cli_routes.document()
    assert doc["sections"] == ["The loop", "Subjects", "Evals", "Analysis and data"]
    roots = {root["name"]: root for root in doc["roots"]}
    assert roots["library"] == {
        "name": "library", "section": "Subjects", "aliases": ["product"], "cost": "free"
    }
    assert roots["repo"] == {"name": "repo", "section": "Subjects", "aliases": [], "cost": "paid"}
    assert roots["data"]["section"] == "Analysis and data"
    assert roots["evaluate"]["cost"] == "paid"
    assert {leaf["cost"] for leaf in doc["leaves"]} == {"free", "paid"}


def test_data_schema_list_names_the_kinds(capsys):
    args = parse_args(["data", "schema", "list"])
    args.func(args)
    assert capsys.readouterr().out.split() == ["cli_routes"]


def test_data_schema_show_rejects_an_unknown_kind():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["data", "schema", "show", "nope"])
    assert exc.value.code == 2


def test_the_route_document_validates_against_its_schema():
    assert schema_errors("cli_routes.v1", cli_routes.document()) == []


@pytest.mark.parametrize(
    "damage",
    [
        lambda doc: doc["roots"][0].pop("cost"),
        lambda doc: doc["leaves"][0].update(cost="cheap"),
        lambda doc: doc["leaves"][0].pop("path"),
        lambda doc: doc.pop("tombstones"),
        lambda doc: doc.update(schema_version="cli_routes.v2"),
        lambda doc: doc["roots"][0].update(extra=1),
    ],
    ids=["root-no-cost", "bad-cost", "leaf-no-path", "no-tombstones", "wrong-version", "extra-key"],
)
def test_the_route_schema_rejects_a_document_whose_shape_drifted(damage):
    doc = cli_routes.document()
    damage(doc)
    assert schema_errors("cli_routes.v1", doc)


def test_the_data_help_promises_only_the_kinds_it_has():
    from dokimi.commands.data import SCHEMA_KINDS

    helps = {path: action.help for path, action in _help_actions(build_parser())}
    assert set(SCHEMA_KINDS) == {"cli_routes"}  # widen the help below when a kind is added
    for path in (("data",), ("data", "schema")):
        assert "artifact" not in helps[path].lower(), helps[path]
        assert "route" in helps[path].lower(), helps[path]


def test_only_the_top_level_parser_lists_its_commands_by_section():
    top = build_parser()
    assert "The loop:" in top.format_help()
    sub = _subparsers(top)
    assert sub is not None
    for name, child in sub.choices.items():
        assert type(child) is argparse.ArgumentParser, name
        assert "The loop:" not in child.format_help(), name
