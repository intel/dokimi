# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The flag contract (issue #94 §2.4, stage F1a): ratchet mode over the CLI tree.

Every option string on a parser of ``dokimi.cli.build_parser()`` must be a canonical
spelling in ``dokimi/cli_flags.py`` or a row of ``tests/data/cli_flags_allowlist.txt``. A
banned synonym fails unless it is allowlisted. An allowlist row that no parser uses, or
that is now canonical, fails too, so the list keeps no stale row. The snapshot
``docs/reference/cli-interface.json`` must equal the dump of ``scripts/cli_surface.py``
(the #88 dumper); ``make cli-snapshot`` regenerates it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from dokimi import cli, cli_flags

PROJECT = Path(__file__).resolve().parents[1]
ALLOWLIST = PROJECT / "tests" / "data" / "cli_flags_allowlist.txt"
SNAPSHOT = PROJECT / "docs" / "reference" / "cli-interface.json"
_ALLOWLIST_REL = "dokimi/tests/data/cli_flags_allowlist.txt"
_REGISTRY_REL = "dokimi/dokimi/cli_flags.py"
# The snapshot is written by the pinned interpreter (`make dev`). The dumper hashes validator
# source with ast.dump, whose output differs between Python versions, so the content check runs
# on that interpreter only. CI runs it there in the `typecheck` job, on every PR; the other
# matrix legs keep the ratchet and the structural checks.
PINNED_PYTHON = (3, 13)

_spec = importlib.util.spec_from_file_location("cli_surface", PROJECT / "scripts" / "cli_surface.py")
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


@dataclass(frozen=True)
class Use:
    """One option string on one parser, with what the parser does with it."""

    leaf: str
    option: str
    action: str
    type: str
    dest: str


def _type_name(type_: object) -> str:
    if type_ is None:
        return "str"
    return getattr(type_, "__name__", type(type_).__name__)


def uses(parser: argparse.ArgumentParser) -> list[Use]:
    """Every option string of ``parser`` and of its subcommands, root first, in parser order.

    ``-h/--help`` and ``--version`` are left out: argparse adds them to every parser.
    """
    found: list[Use] = []

    def walk(node: argparse.ArgumentParser, path: str) -> None:
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in sorted(action.choices.items()):
                    walk(sub, f"{path} {name}")
            elif not isinstance(action, (argparse._HelpAction, argparse._VersionAction)):
                for option in action.option_strings:
                    found.append(Use(path, option, type(action).__name__,
                                     _type_name(action.type), action.dest))

    walk(parser, "dokimi")
    return found


def load_allowlist(path: Path = ALLOWLIST) -> dict[str, str]:
    """Allowlisted spelling -> reason ("" for a row that has none). ``#`` starts a comment."""
    rows: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        spelling, _, reason = raw.partition("#")
        spelling = spelling.strip()
        if spelling:
            rows[spelling] = reason.strip()
    return rows


def violations(found: Sequence[Use], allowlist: Mapping[str, str],
               concepts: Iterable[cli_flags.Concept] = cli_flags.CONCEPTS) -> list[str]:
    """Every break of the ratchet, as a message that says how to fix it. Empty when clean."""
    concepts = tuple(concepts)
    canonical = {f.spelling: c for c in concepts for f in c.canonical}
    banned = {s: c for c in concepts for s in c.banned}
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for use in found:
        if (use.leaf, use.option) in seen:
            continue
        seen.add((use.leaf, use.option))
        if use.option in canonical or use.option in allowlist:
            continue
        concept = banned.get(use.option)
        if concept is not None and concept.canonical:
            spellings = ", ".join(f"`{f.spelling}`" for f in concept.canonical)
            problems.append(
                f"{use.leaf}: `{use.option}` is a banned synonym of `{concept.name}`. Use "
                f"{spellings} ({_REGISTRY_REL}). If the old spelling must stay, add "
                f"`{use.option}  # <reason>` to {_ALLOWLIST_REL}.")
        else:
            problems.append(
                f"{use.leaf}: `{use.option}` is neither a canonical spelling in {_REGISTRY_REL} "
                f"nor on {_ALLOWLIST_REL}. Use the registry spelling of its concept, or, with a "
                f"reason, add `{use.option}  # <reason>` to {_ALLOWLIST_REL}. Then run "
                "`make cli-snapshot` if the CLI surface changed.")
    used = {use.option for use in found}
    for spelling in sorted(allowlist):
        if spelling not in used:
            problems.append(f"{_ALLOWLIST_REL}: `{spelling}` is used by no parser any more; "
                            "delete the row.")
        elif spelling in canonical:
            problems.append(f"{_ALLOWLIST_REL}: `{spelling}` is a canonical spelling now; "
                            "delete the row.")
    return problems


def _tree_violations() -> list[str]:
    return violations(uses(cli.build_parser()), load_allowlist())


def _subparser(parser: argparse.ArgumentParser, *path: str) -> argparse.ArgumentParser:
    node = parser
    for name in path:
        (sub,) = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
        node = sub.choices[name]
    return node


def test_registry_data_is_consistent():
    assert cli_flags.registry_problems() == []


def test_every_spelling_on_the_tree_is_canonical_or_allowlisted():
    problems = _tree_violations()
    assert problems == [], "\n".join(problems)


def test_the_walk_covers_the_whole_tree():
    found = uses(cli.build_parser())
    leaves = {use.leaf for use in found}
    assert {"dokimi run", "dokimi repo scan"} <= leaves
    assert len({use.option for use in found}) > 200
    assert all(use.dest for use in found)


def test_a_new_flag_outside_the_registry_fails_the_ratchet(monkeypatch):
    real = cli.build_parser

    def with_new_flag() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "run").add_argument("--sample-size", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_new_flag)
    problems = _tree_violations()
    assert any("dokimi run: `--sample-size` is neither a canonical spelling" in p
               and f"add `--sample-size  # <reason>` to {_ALLOWLIST_REL}" in p for p in problems), problems


def test_a_new_banned_synonym_fails_and_names_the_registry_spelling(monkeypatch):
    real = cli.build_parser

    def with_banned_flag() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "run").add_argument("--trials", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_banned_flag)
    problems = _tree_violations()
    assert any("`--trials` is a banned synonym of `reps`" in p and "`--reps`" in p for p in problems), problems


def test_an_allowlisted_banned_spelling_still_passes_on_a_new_leaf(monkeypatch):
    # Only the new leaf is under test: the tree's violations must not change.
    real = cli.build_parser
    before = violations(uses(real()), load_allowlist())

    def on_another_leaf() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "repo", "scan").add_argument("--jobs", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", on_another_leaf)
    assert _tree_violations() == before


def test_a_banned_synonym_is_named_with_its_canonical_spelling():
    fixture = (cli_flags.Concept(name="output", help="", canonical=(cli_flags.Flag("--out"),),
                                 banned=("--outfile",)),)
    found = [Use("dokimi run", "--outfile", "_StoreAction", "str", "outfile")]
    (problem,) = violations(found, {}, fixture)
    assert "banned synonym of `output`" in problem and "`--out`" in problem


def test_a_banned_spelling_of_an_undecided_concept_is_not_enforced_as_banned():
    fixture = (cli_flags.Concept(name="max_tokens", help="", canonical=(), banned=("--max-tokens",)),)
    found = [Use("dokimi run", "--max-tokens", "_StoreAction", "int", "max_tokens")]
    (problem,) = violations(found, {}, fixture)
    assert "banned synonym" not in problem
    assert "is neither a canonical spelling" in problem


def test_allowlist_rows_that_are_unused_or_canonical_fail():
    fixture = (cli_flags.Concept(name="output", help="", canonical=(cli_flags.Flag("--out"),)),)
    found = [Use("dokimi run", "--out", "_StoreAction", "str", "out")]
    problems = violations(found, {"--gone": "", "--out": ""}, fixture)
    assert any("`--gone` is used by no parser" in p for p in problems)
    assert any("`--out` is a canonical spelling now" in p for p in problems)
    assert len(problems) == 2


@pytest.mark.skipif(sys.version_info[:2] != PINNED_PYTHON,
                    reason="the content check runs on the pinned Python, in CI's typecheck job")
def test_the_cli_interface_snapshot_is_current():
    stored = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert cs.dump_surface() == stored, (
        f"{SNAPSHOT.relative_to(PROJECT.parent)} is stale: run `make cli-snapshot` in dokimi/ "
        "and commit the result")


def test_the_snapshot_check_notices_a_new_flag(monkeypatch):
    # Both dumps come from this interpreter, so the check holds on every Python version.
    before = cs.dump_surface()
    real = cli.build_parser

    def with_new_flag() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "run").add_argument("--sample-size", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_new_flag)
    assert cs.dump_surface() != before
