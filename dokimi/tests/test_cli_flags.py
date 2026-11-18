# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The flag contract (issue #94 §2.4, stage F1a): ratchet mode over the CLI tree.

Every option string on a parser of ``dokimi.cli.build_parser()`` must be a canonical
spelling in ``dokimi/cli_flags.py`` or a row of ``tests/data/cli_flags_allowlist.txt``. A
banned synonym fails unless it is allowlisted. An allowlist row that no parser uses, or
that is now canonical, fails too, so the list keeps no stale row. A canonical spelling must also
mean its registry row on every leaf (switch or value, metavar, choices, type): ``meaning_problems``
checks that, and a clash is listed in ``MEANING_EXCEPTIONS`` (leaf, spelling, aspect) with a reason. The snapshot
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
    """One option string on one parser, with what the parser does with it.

    ``nargs`` is 0 for a switch, ``metavar`` is the explicit one (None when not set) and
    ``choices`` contains the parser's fixed set of values.
    """

    leaf: str
    option: str
    action: str
    type: str
    dest: str
    nargs: object = None
    metavar: str | None = None
    choices: frozenset[str] = frozenset()


def _type_name(type_: object) -> str:
    if type_ is None:
        return "str"
    return getattr(type_, "__name__", type(type_).__name__)


def _metavar_text(metavar: object) -> str | None:
    if isinstance(metavar, tuple):
        return " ".join(str(part) for part in metavar)
    return metavar if isinstance(metavar, str) else None


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
                metavar = _metavar_text(action.metavar)
                for option in action.option_strings:
                    found.append(Use(path, option, type(action).__name__,
                                     _type_name(action.type), action.dest,
                                     action.nargs, metavar, frozenset(map(str, action.choices or ()))))

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


# Types a parser may use for each registry kind. A kind not listed takes any type.
_TYPES_BY_KIND: Mapping[str, frozenset[str]] = {
    "int": frozenset({"int", "positive_int"}),
    "float": frozenset({"float"}),
    "usd": frozenset({"positive_usd", "_usd"}),
}

# Known clashes, keyed by (leaf, spelling, aspect): each waits for a later F1b migration and
# changes no CLI behaviour now. A new clash fails unless its key is here.
MEANING_EXCEPTIONS: dict[tuple[str, str, str], str] = {
    ("dokimi arms run", "--judge", "switch"): (
        "a switch that adds the LLM-as-judge; the registry's role_models `--judge` takes "
        "PROVIDER/MODEL. The clash issue #94 names (sec. 2.4, D10); F1b moves this leaf to `--judge P/M`"),
    ("dokimi arms matrix-run", "--judge", "switch"): (
        "a switch that adds the LLM-as-judge; the registry's role_models `--judge` takes "
        "PROVIDER/MODEL. The clash issue #94 names (sec. 2.4, D10); F1b moves this leaf to `--judge P/M`"),
    ("dokimi subjects run", "--judge", "switch"): (
        "a switch that adds the LLM-as-judge; the registry's role_models `--judge` takes "
        "PROVIDER/MODEL. The clash issue #94 names (sec. 2.4, D10); F1b moves this leaf to `--judge P/M`"),
    ("dokimi eval tasks generate", "--from", "choices"): (
        "a source kind (graph, prs, replay; #94's owner decision of 11-01 lists history where "
        "the code has prs); the registry's `--from RUN` is the inputs concept. Decide the row at F1b"),
    ("dokimi repo reach", "--from", "metavar"): (
        "takes a REACH_JSON file, not a RUN; the registry's `--from RUN` is the inputs concept. "
        "Not one of the #94 audit rows: rename it or split the concept at F1b"),
    ("dokimi eval import", "--format", "choices"): (
        "forces an input format (no choices); the registry's `--format` is a render format "
        "(output concept). Not one of the #94 audit rows; split at F1b"),
    ("dokimi trace consistency", "--arm", "metavar"): (
        "LABEL=GLOB selects traces by glob, not the `[NAME=]REF` of the arm concept. Not one of "
        "the #94 audit rows; rename at F1b"),
    ("dokimi eval check", "--cache", "metavar"): (
        "metavar NAME=PATH where the registry says NAME=DIR: wording only; F1b aligns it"),
    ("dokimi repo run", "--cache", "metavar"): (
        "metavar NAME=PATH where the registry says NAME=DIR: wording only; F1b aligns it"),
    ("dokimi tasks run", "--network", "metavar"): (
        "repeatable PHASE=MODE, with no `:hosts` form; the registry's `--network` is one "
        "`[PHASE=]MODE[:hosts]`. F1b aligns it"),
    ("dokimi eval triggers generate", "--questions", "metavar"): (
        "repeatable PATH=LABEL pairs; the registry's `--questions FILE` is one file. F1b aligns it"),
}


def _registered_choices(value: str) -> frozenset[str]:
    if value.startswith("{") and value.endswith("}"):
        return frozenset(part.strip() for part in value[1:-1].split(","))
    if "|" in value:
        return frozenset(part.strip() for part in value.split("|"))
    return frozenset()


def meaning_clashes(found: Sequence[Use],
                    concepts: Iterable[cli_flags.Concept] = cli_flags.CONCEPTS,
                    ) -> list[tuple[str, str, str, str]]:
    """(leaf, spelling, aspect, what clashes) for every use of a canonical spelling that disagrees
    with its registry row. Aspects: ``switch`` (a switch where the kind takes a value, or the
    reverse), ``metavar`` (set, and differs from ``value``), ``choices`` (choices where the kind is
    not ``choice``, or a missing or divergent set for a ``choice`` row) and ``type``
    (outside ``_TYPES_BY_KIND``). A flag's
    repeatability is not compared.
    """
    flags = {flag.spelling: flag for concept in concepts for flag in concept.canonical}
    clashes: list[tuple[str, str, str, str]] = []
    for use in found:
        flag = flags.get(use.option)
        if flag is None:
            continue
        where = (use.leaf, use.option)
        if (flag.kind == "flag") != (use.nargs == 0):
            shape = "a switch" if use.nargs == 0 else "a value"
            clashes.append((*where, "switch", f"is {shape} here, but the registry kind is `{flag.kind}`"))
        if flag.kind != "flag" and use.metavar is not None and use.metavar != flag.value:
            clashes.append((*where, "metavar", f"has metavar `{use.metavar}`, the registry says `{flag.value}`"))
        if (flag.kind == "choice") != bool(use.choices):
            has = "has choices" if use.choices else "has no choices"
            clashes.append((*where, "choices", f"{has}, but the registry kind is `{flag.kind}`"))
        elif use.choices:
            expected = _registered_choices(flag.value)
            if expected and use.choices != expected:
                clashes.append((*where, "choices", f"has choices {sorted(use.choices)}, "
                                f"the registry lists {sorted(expected)}"))
        allowed = _TYPES_BY_KIND.get(flag.kind)
        if allowed is not None and use.type not in allowed:
            clashes.append((*where, "type", f"has type `{use.type}`, kind `{flag.kind}` takes "
                                            f"{' or '.join(sorted(allowed))}"))
    return clashes


def meaning_problems(found: Sequence[Use], exceptions: Mapping[tuple[str, str, str], str] = MEANING_EXCEPTIONS,
                     concepts: Iterable[cli_flags.Concept] = cli_flags.CONCEPTS) -> list[str]:
    """Each clash that no exception covers, plus each exception that no longer clashes. Empty when clean."""
    clashes = meaning_clashes(found, concepts)
    problems = [
        f"{leaf}: `{option}` {what}. Use the registry meaning, or add the row to "
        f"MEANING_EXCEPTIONS in dokimi/tests/test_cli_flags.py with a reason."
        for leaf, option, aspect, what in clashes if (leaf, option, aspect) not in exceptions
    ]
    clashing = {(leaf, option, aspect) for leaf, option, aspect, _ in clashes}
    problems += [f"MEANING_EXCEPTIONS: `{option}` on `{leaf}` ({aspect}) no longer clashes; delete the row."
                 for leaf, option, aspect in sorted(set(exceptions) - clashing)]
    problems += [f"MEANING_EXCEPTIONS: `{option}` on `{leaf}` ({aspect}) has no reason; say why it stays."
                 for (leaf, option, aspect), reason in exceptions.items() if not reason.strip()]
    return problems


def _tree_meaning_problems() -> list[str]:
    return meaning_problems(uses(cli.build_parser()))


def _subparser(parser: argparse.ArgumentParser, *path: str) -> argparse.ArgumentParser:
    node = parser
    for name in path:
        (sub,) = [a for a in node._actions if isinstance(a, argparse._SubParsersAction)]
        node = sub.choices[name]
    return node


def test_registry_data_is_consistent():
    assert cli_flags.registry_problems() == []


def _concept(name: str, canonical: Sequence[str] = (), banned: Sequence[str] = ()) -> cli_flags.Concept:
    return cli_flags.Concept(name=name, help=name,
                             canonical=tuple(cli_flags.Flag(s) for s in canonical),
                             banned=tuple(banned))


@pytest.mark.parametrize(("concepts", "message"), [
    ([_concept("a", canonical=["out"])], "a: canonical `out` is not an option"),
    ([_concept("a", canonical=["--out"]), _concept("b", canonical=["--out"])],
     "`--out` is canonical in both a and b"),
    ([_concept("a", banned=["-o"])], "a: banned `-o` is not a long option"),
    ([_concept("a", canonical=["--out"]), _concept("b", banned=["--out"])],
     "`--out` is canonical in a and banned in b"),
    ([_concept("a", banned=["--old"]), _concept("b", banned=["--old"])],
     "`--old` is banned in both a and b"),
])
def test_registry_problems_flags_each_inconsistency(concepts, message):
    assert message in cli_flags.registry_problems(concepts)


def test_every_spelling_on_the_tree_is_canonical_or_allowlisted():
    problems = _tree_violations()
    assert problems == [], "\n".join(problems)


def test_every_registered_flag_has_one_meaning_on_the_tree():
    problems = _tree_meaning_problems()
    assert problems == [], "\n".join(problems)


def test_meaning_clashes_name_each_disagreement_with_its_registry_row():
    concepts = (cli_flags.Concept(name="x", help="", canonical=(
        cli_flags.Flag("--n", "N", "int"),
        cli_flags.Flag("--sw", kind="flag"),
        cli_flags.Flag("--pick", "A|B", "choice"),
        cli_flags.Flag("--name", "NAME", "text"),
    )),)
    found = [
        Use("dokimi a", "--n", "_StoreAction", "str", "n", None, "SEED"),
        Use("dokimi a", "--sw", "_StoreAction", "str", "sw", None, None),
        Use("dokimi a", "--pick", "_StoreAction", "str", "pick", None, None),
        Use("dokimi a", "--name", "_StoreAction", "str", "name", None, "NAME", frozenset({"name"})),
        Use("dokimi a", "--name", "_StoreAction", "str", "name", None, "NAME"),
    ]
    text = [what for _leaf, _option, _aspect, what in meaning_clashes(found, concepts)]
    assert text == [
        "has metavar `SEED`, the registry says `N`",
        "has type `str`, kind `int` takes int or positive_int",
        "is a value here, but the registry kind is `flag`",
        "has no choices, but the registry kind is `choice`",
        "has choices, but the registry kind is `text`",
    ]
    # The other direction: a switch where the kind takes a value.
    switch = [Use("dokimi a", "--n", "_StoreTrueAction", "int", "n", 0, None)]
    assert [what for *_, what in meaning_clashes(switch, concepts)] == [
        "is a switch here, but the registry kind is `int`"]


@pytest.mark.parametrize(("value", "choices", "clashes"), [
    ("native|none", ["none", "native"], False),
    ("native|none", ["native", "none", "auto"], True),
    ("{warn,fail}", ["warn"], True),
    ("FORMAT", ["json", "text"], False),
])
def test_enumerated_choice_values_match_the_registry(value, choices, clashes):
    concepts = (cli_flags.Concept(name="x", help="", canonical=(cli_flags.Flag("--pick", value, "choice"),)),)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pick", choices=choices)
    assert [aspect for _, _, aspect, _ in meaning_clashes(uses(parser), concepts)] == (
        ["choices"] if clashes else [])


def test_a_usd_flag_requires_a_current_cli_usd_type():
    concepts = (cli_flags.Concept(name="x", help="", canonical=(cli_flags.Flag("--max-usd", "USD", "usd"),)),)
    found = [Use("dokimi a", "--max-usd", "_StoreAction", t, "max_usd", None, None)
             for t in ("positive_usd", "_usd", "float", "usd_amount")]
    assert [what for *_, what in meaning_clashes(found, concepts)] == [
        "has type `float`, kind `usd` takes _usd or positive_usd",
        "has type `usd_amount`, kind `usd` takes _usd or positive_usd",
    ]


def test_a_float_flag_takes_float_and_not_an_int():
    concepts = (cli_flags.Concept(name="x", help="", canonical=(cli_flags.Flag("--rate", "RATE", "float"),)),)
    found = [Use("dokimi a", "--rate", "_StoreAction", t, "rate", None, "RATE") for t in ("float", "int")]
    assert [what for *_, what in meaning_clashes(found, concepts)] == [
        "has type `int`, kind `float` takes float"]


def test_a_new_use_that_clashes_fails_and_an_exception_is_leaf_scoped(monkeypatch):
    real = cli.build_parser

    def with_judge_switch() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "docs", "check").add_argument("--judge", action="store_true")
        return parser

    monkeypatch.setattr(cli, "build_parser", with_judge_switch)
    problems = _tree_meaning_problems()
    assert problems == [
        "dokimi docs check: `--judge` is a switch here, but the registry kind is `model_ref`. Use the "
        "registry meaning, or add the row to MEANING_EXCEPTIONS in dokimi/tests/test_cli_flags.py with a reason."
    ], problems


def test_a_meaning_exception_that_no_longer_clashes_fails():
    found = [Use("dokimi arms run", "--judge", "_StoreAction", "_judge_ref", "judge", None, "PROVIDER/MODEL")]
    problems = meaning_problems(found, {("dokimi arms run", "--judge", "switch"): "reason"})
    assert problems == ["MEANING_EXCEPTIONS: `--judge` on `dokimi arms run` (switch) no longer clashes; "
                        "delete the row."]


def test_the_walk_covers_the_whole_tree():
    found = uses(cli.build_parser())
    leaves = {use.leaf for use in found}
    assert {"dokimi docs check", "dokimi repo scan"} <= leaves
    assert len({use.option for use in found}) > 200
    assert all(use.dest for use in found)


def test_a_new_flag_outside_the_registry_fails_the_ratchet(monkeypatch):
    real = cli.build_parser

    def with_new_flag() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "docs", "check").add_argument("--sample-size", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_new_flag)
    problems = _tree_violations()
    assert any("dokimi docs check: `--sample-size` is neither a canonical spelling" in p
               and f"add `--sample-size  # <reason>` to {_ALLOWLIST_REL}" in p for p in problems), problems


def test_a_new_banned_synonym_fails_and_names_the_registry_spelling(monkeypatch):
    real = cli.build_parser

    def with_banned_flag() -> argparse.ArgumentParser:
        parser = real()
        _subparser(parser, "docs", "check").add_argument("--trials", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_banned_flag)
    # `--trials` is allowlisted for catalog trigger-probe (a real use on main), so the probe
    # runs with that row removed: a new leaf must not get the exemption.
    allow = {k: v for k, v in load_allowlist().items() if k != "--trials"}
    problems = violations(uses(cli.build_parser()), allow)
    # Anchored on the injected leaf: catalog trigger-probe's own --trials gives the same text.
    assert any(
        p.startswith("dokimi docs check: ") and "`--trials` is a banned synonym of `reps`" in p and "`--reps`" in p
        for p in problems
    ), problems


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
        _subparser(parser, "docs", "check").add_argument("--sample-size", type=int, default=1)
        return parser

    monkeypatch.setattr(cli, "build_parser", with_new_flag)
    assert cs.dump_surface() != before


def test_an_exception_covers_its_aspect_only_on_its_pair():
    # The excepted switch clash on `--judge` stays excepted; a value use of the same spelling on
    # the same leaf is a different clash (metavar), and it is reported.
    found = [
        Use("dokimi arms run", "--judge", "_StoreTrueAction", "bool", "judge", 0, None),
        Use("dokimi arms run", "--judge", "_StoreAction", "int", "judge", None, "N"),
    ]
    exceptions = {("dokimi arms run", "--judge", "switch"): "reason"}
    problems = meaning_problems(found, exceptions)
    assert problems == [
        "dokimi arms run: `--judge` has metavar `N`, the registry says `PROVIDER/MODEL`. Use the "
        "registry meaning, or add the row to MEANING_EXCEPTIONS in dokimi/tests/test_cli_flags.py with a reason."
    ], problems


def test_a_meaning_exception_needs_a_reason():
    found = [Use("dokimi arms run", "--judge", "_StoreTrueAction", "bool", "judge", 0, None)]
    problems = meaning_problems(found, {("dokimi arms run", "--judge", "switch"): " "})
    assert problems == ["MEANING_EXCEPTIONS: `--judge` on `dokimi arms run` (switch) has no reason; "
                        "say why it stays."]


def test_a_tuple_metavar_is_compared_as_one_text():
    assert _metavar_text(("A", "B")) == "A B"
    assert _metavar_text("N") == "N"
    assert _metavar_text(None) is None
