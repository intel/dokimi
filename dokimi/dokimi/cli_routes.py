# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The CLI route table: where each root and leaf is listed in ``dokimi --help``, whether it
can spend money, and which old paths are tombstones.

``dokimi.cli`` reads the table to build the sectioned help and the ``[free]``/``[paid]``
labels, and ``dokimi data schema show cli_routes`` prints it. Help text stays with each
command's parser; the table gives a one-line help only for a route whose parser has none.

Cost: ``paid`` means the route can make a model call (an LLM, or an agent harness) in some
flag combination, and ``free`` means it never does. A root is ``paid`` when any leaf under
it is. The sections follow issue #94 §2.1. The table describes today's routes: a root sits in
the section of the operation it will hold once the renames of #94 §4 land.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from dokimi.cli_flags import FLAG_MOVES

SECTIONS: tuple[str, ...] = ("The loop", "Subjects", "Evals", "Analysis and data")

FREE = "free"
PAID = "paid"


@dataclass(frozen=True)
class Root:
    """A top-level name. ``aliases`` are alternative names for it."""

    name: str
    section: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Leaf:
    """An operation: a parser with no subcommands. ``path`` is its argv, e.g. ``("eval", "score", "pointwise")``."""

    path: tuple[str, ...]
    cost: str
    summary: str | None = None  # one-line help, used only when the parser has none


@dataclass(frozen=True)
class Tombstone:
    """An old argv prefix that now moved: it answers with ``new`` and exits 2, never forwards.

    ``flags`` are inserted after ``new`` in the printed argv, for a move that also sets a flag.

    A group that stays in place (``eval score``) is the exception when no option follows it: see
    ``moved_argv``.
    """

    old: tuple[str, ...]
    new: tuple[str, ...]
    flags: tuple[str, ...] = ()
    # For a move whose argv changes shape (a flag becomes a positional): the argv after ``old``
    # becomes the whole printed argv. It replaces ``new``, ``flags`` and the rest of the argv.
    rewrite: Callable[[list[str]], list[str]] | None = field(default=None, compare=False)


ROOTS: tuple[Root, ...] = (
    Root("evaluate", "The loop"),
    Root("baseline", "The loop"),
    Root("report", "The loop"),
    Root("answers", "The loop"),
    Root("benchmark", "The loop"),
    Root("dashboard", "The loop"),
    Root("arms", "The loop"),
    Root("subjects", "The loop"),
    Root("tasks", "The loop"),
    Root("explore", "The loop"),
    Root("catalog", "Subjects"),
    Root("docs", "Subjects"),
    Root("skill", "Subjects"),
    Root("novelty", "Subjects"),
    Root("repo", "Subjects"),
    Root("eval", "Evals"),
    Root("tools", "Analysis and data"),
    Root("trace", "Analysis and data"),
    Root("runs", "Analysis and data"),
    Root("backends", "Analysis and data"),
    Root("data", "Analysis and data"),
)

LEAVES: tuple[Leaf, ...] = (
    Leaf(("evaluate",), PAID),
    Leaf(("baseline", "save"), FREE),
    Leaf(("baseline", "list"), FREE),
    Leaf(("baseline", "compare"), FREE),
    Leaf(("report", "eval"), FREE),
    Leaf(("report", "generate"), FREE),
    Leaf(("report", "model-compare"), FREE),
    Leaf(("answers", "generate"), PAID),
    Leaf(("benchmark", "run"), PAID),
    Leaf(("benchmark", "preflight"), FREE),
    Leaf(("benchmark", "batch"), PAID),
    Leaf(("dashboard", "generate"), FREE),
    Leaf(("arms", "run"), PAID),
    Leaf(("arms", "matrix-run"), PAID),
    Leaf(("arms", "plugin-delta"), FREE),
    Leaf(("subjects", "run"), PAID),
    Leaf(("subjects", "evidence"), FREE),
    Leaf(("tasks", "harnesses"), FREE),
    Leaf(("tasks", "run"), PAID),
    Leaf(("explore", "list"), FREE),
    Leaf(("explore", "run"), PAID),
    Leaf(("catalog", "list"), FREE),
    Leaf(("catalog", "show"), FREE),
    Leaf(("catalog", "intents"), FREE),
    Leaf(("catalog", "check"), FREE),
    Leaf(("catalog", "eval"), FREE),
    Leaf(("catalog", "formats"), FREE),
    Leaf(("catalog", "trigger-probe"), PAID),
    Leaf(("docs", "check"), FREE, "Run the static benchmark spec and save a snapshot"),
    Leaf(("docs", "compare"), FREE, "Compare two saved benchmark snapshots"),
    Leaf(("skill", "novelty", "analyze"), PAID),
    Leaf(("skill", "novelty", "atoms"), PAID),
    Leaf(("skill", "novelty", "probe"), PAID),
    Leaf(("skill", "novelty", "items"), FREE),
    Leaf(("skill", "shorten"), PAID),
    Leaf(("skill", "check"), FREE),
    Leaf(("skill", "routing"), PAID),
    Leaf(("catalog", "routing"), PAID),
    Leaf(("novelty", "view"), FREE),
    Leaf(("repo", "validate"), FREE),
    Leaf(("repo", "scan"), FREE),
    Leaf(("repo", "inspect"), FREE),
    Leaf(("repo", "fetch"), FREE),
    Leaf(("repo", "reach"), PAID),
    Leaf(("repo", "skill-reach"), PAID),
    Leaf(("repo", "budget"), FREE),
    Leaf(("repo", "run"), PAID),
    Leaf(("repo", "report"), FREE),
    Leaf(("repo", "compare"), FREE),
    Leaf(("repo", "hits", "judge"), PAID),
    Leaf(("repo", "hits", "sheet"), FREE),
    Leaf(("eval", "inspect"), FREE),
    Leaf(("eval", "check"), FREE),
    Leaf(("eval", "import"), FREE),
    Leaf(("eval", "native-import"), FREE),
    Leaf(("eval", "tasks", "list"), FREE),
    Leaf(("eval", "tasks", "generate"), FREE),
    Leaf(("eval", "personas", "discover"), PAID),
    Leaf(("eval", "personas", "validate"), FREE),
    Leaf(("eval", "questions", "generate"), PAID),
    Leaf(("eval", "questions", "analyze"), PAID),
    Leaf(("eval", "questions", "refine"), PAID),
    Leaf(("eval", "questions", "review"), PAID),
    Leaf(("eval", "triggers", "generate"), PAID),
    Leaf(("eval", "score", "pointwise"), PAID),
    Leaf(("eval", "score", "pairwise"), PAID),
    Leaf(("eval", "score", "panel"), PAID),
    Leaf(("eval", "score", "checklist"), PAID),
    Leaf(("eval", "score", "grounding"), FREE),
    Leaf(("eval", "score", "ragas"), PAID),
    Leaf(("eval", "score", "reference"), PAID),
    Leaf(("eval", "judge", "validate"), PAID),
    Leaf(("eval", "judge", "wording"), PAID),
    Leaf(("eval", "judge", "agreement"), PAID),
    Leaf(("eval", "coverage"), FREE),
    Leaf(("eval", "headroom"), FREE),
    Leaf(("eval", "leak"), FREE),
    Leaf(("eval", "redteam"), PAID),
    Leaf(("eval", "skill-coverage"), PAID),
    Leaf(("data", "export"), FREE),
    Leaf(("data", "import", "docling"), FREE),
    Leaf(("tools", "preference"), PAID),
    Leaf(("trace", "inspect"), FREE),
    Leaf(("trace", "consistency"), FREE),
    Leaf(("trace", "prefilter"), FREE),
    Leaf(("runs", "list"), FREE),
    Leaf(("runs", "show"), FREE),
    Leaf(("backends",), FREE),
    Leaf(("data", "schema", "list"), FREE),
    Leaf(("data", "schema", "show"), FREE),
)

def _novelty_routing_argv(rest: list[str]) -> list[str]:
    """The argv `novelty routing` moved to, given the argv after it (#94 M3).

    ``--all-vs-all`` moves to ``catalog routing ROOT`` (its ``--catalog`` value becomes ROOT), and needs a
    ``--catalog``: a competitors-only call has no replacement. Any other call moves to ``skill routing SKILL``
    (its ``--artifact`` value becomes SKILL, ``--catalog`` becomes ``--against``). A bare ``--competitors``
    path was text, so it moves as ``text:``. ``--repeats`` is ``--reps`` in both. A flag the new leaf does
    not take stays in the printed argv, so the new parser refuses it. A value flag with no value (none
    follows, or a flag does) stays there too. A plain token right after an ``--artifact`` or ``--catalog``
    value, after the ``=`` form of one, after ``--all-vs-all``, or after ``--competitors=`` is refused, as the
    retired parser refused it. An ambiguous prefix raises
    :class:`AmbiguousRetiredFlag`, as no command can refuse it.
    """
    rest = [_retired_spelling(tok) for tok in rest]
    values, kept = _split_routing_argv(rest)
    all_vs_all = "--all-vs-all" in rest
    # argparse keeps the last of a repeated option, so the rewrite does too.
    artifact = [_legacy_artifact(values["--artifact"][-1])] if values["--artifact"] else []
    if all_vs_all:
        if not values["--catalog"]:
            raise RetiredFlagError("novelty routing --all-vs-all without --catalog has no replacement: "
                                   "catalog routing needs a ROOT, and the skills come from it")
        stray = ["--artifact", values["--artifact"][-1]] if values["--artifact"] else []
        return ["catalog", "routing", *values["--catalog"][-1:], *kept, *stray]
    against = ["--against", values["--catalog"][-1]] if values["--catalog"] else []
    return ["skill", "routing", *artifact, *against, *kept]


def _split_routing_argv(rest: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """The values of --artifact and --catalog, and the argv that stays (the other flags, in order)."""
    values: dict[str, list[str]] = {"--artifact": [], "--catalog": []}
    kept: list[str] = []
    i = 0
    while i < len(rest):
        name, eq, value = rest[i].partition("=")
        if name == "--all-vs-all":
            _refuse_stray(rest, i + 1)
            i += 1
        elif name in values:
            i = _take_value_flag(rest, i, name, eq, value, values, kept)
        elif name == "--competitors":
            i = _take_competitors(rest, i, eq, value, kept)
        else:
            kept.append("--reps" + eq + value if name == "--repeats" else rest[i])
            i += 1
    return values, kept


def _is_plain(token: str) -> bool:
    """argparse reads a bare `-` and any token not starting with `-` as a value or positional, not a flag."""
    return token == "-" or not token.startswith("-")


def _refuse_stray(rest: list[str], j: int) -> None:
    """Refuse a plain token where no retired flag takes one: the retired parser said unrecognized arguments."""
    if j < len(rest) and _is_plain(rest[j]):
        raise RetiredFlagError(f"unrecognized arguments: {rest[j]}")


def _take_value_flag(rest: list[str], i: int, name: str, eq: str, value: str,
                     values: dict[str, list[str]], kept: list[str]) -> int:
    """Take one --artifact or --catalog and its value; returns the next index."""
    if eq:
        values[name].append(value)
        _refuse_stray(rest, i + 1)
        return i + 1
    if i + 1 < len(rest) and _is_plain(rest[i + 1]):
        values[name].append(rest[i + 1])
        _refuse_stray(rest, i + 2)
        return i + 2
    # No value follows: the flag stays in the printed argv, and the new leaf refuses it, since it has
    # no such flag (its error text is not the retired parser's).
    kept.append(rest[i])
    return i + 1


def _take_competitors(rest: list[str], i: int, eq: str, value: str, kept: list[str]) -> int:
    """Take --competitors and the values that follow it (nargs="+"); a bare path was text. Returns the next index.

    The `=` form takes one value only, so a plain token after it is stray, as argparse refused it.
    """
    if eq:
        kept.append(f"--competitors={_legacy_competitor(value)}")
        _refuse_stray(rest, i + 1)
        return i + 1
    kept.append(rest[i])
    i += 1
    while i < len(rest) and _is_plain(rest[i]):
        kept.append(_legacy_competitor(rest[i]))
        i += 1
    return i


# The flags `novelty routing` took (its parser allowed any unique prefix of one).
_RETIRED_ROUTING_FLAGS = (
    "--all-vs-all", "--artifact", "--catalog", "--competitors", "--estimate-only",
    "--include-disabled", "--include-invalid", "--keep-copies-under", "--max-tokens", "--max-usd",
    "--model", "--out", "--provider", "--queries", "--queries-by-skill", "--queries-from-headings",
    "--repeats", "--seed",
)


class RetiredFlagError(ValueError):
    """A retired call the retired parser refused, or that has no replacement: no command is printed."""


class AmbiguousRetiredFlag(RetiredFlagError):
    """A prefix that named two retired flags: the retired parser refused it, so no command is printed."""


def _retired_spelling(token: str) -> str:
    """A token with a retired flag's prefix spelt in full, as argparse would have read it.

    An ambiguous prefix raises: the new leaf may take it as one of its own flags (`--c` is
    `--competitors` on `skill routing`), so it cannot be left for the printed command to refuse.
    """
    name, eq, value = token.partition("=")
    if not name.startswith("--") or name in _RETIRED_ROUTING_FLAGS or name == "--":
        return token
    hits = [flag for flag in _RETIRED_ROUTING_FLAGS if flag.startswith(name)]
    if len(hits) > 1:
        raise AmbiguousRetiredFlag(f"ambiguous option: {name} could match {', '.join(hits)}")
    return hits[0] + eq + value if hits else token


def _legacy_artifact(ref: str) -> str:
    """A retired `--artifact` value as the new SKILL: a bare path was always text, so it stays text."""
    return ref if ref.startswith(("skill:", "text:")) else f"text:{ref}"


def _legacy_competitor(ref: str) -> str:
    """A retired `--competitors` value: a bare path was text on the retired command, and is still text."""
    return _legacy_artifact(ref)


# The moves of #94 M1-M3 and D11. Not yet: `novelty view` (the proposal maps it to `report $RUN`).
# A bare retired root comes last: the first match wins, and a root matches every path under it.
TOMBSTONES: tuple[Tombstone, ...] = (
    Tombstone(("tasks", "inspect"), ("eval", "inspect")),
    Tombstone(("repo", "tasks", "check"), ("eval", "check")),
    Tombstone(("personas", "discover"), ("eval", "personas", "discover")),
    Tombstone(("personas", "approve"), ("eval", "personas", "validate")),
    Tombstone(("questions", "generate"), ("eval", "questions", "generate")),
    Tombstone(("questions", "analyze"), ("eval", "questions", "analyze")),
    Tombstone(("questions", "refine"), ("eval", "questions", "refine")),
    Tombstone(("questions", "panel-review"), ("eval", "questions", "review")),
    Tombstone(("questions", "import"), ("eval", "import")),
    Tombstone(("tasks", "list"), ("eval", "tasks", "list")),
    Tombstone(("catalog", "trigger-set"), ("eval", "triggers", "generate")),
    Tombstone(("eval", "score"), ("eval", "score", "pointwise")),
    Tombstone(("eval", "pairwise"), ("eval", "score", "pairwise")),
    Tombstone(("eval", "panel-score"), ("eval", "score", "panel")),
    Tombstone(("eval", "checklist"), ("eval", "score", "checklist")),
    Tombstone(("eval", "grounding"), ("eval", "score", "grounding")),
    Tombstone(("eval", "ragas"), ("eval", "score", "ragas")),
    Tombstone(("eval", "reference"), ("eval", "score", "reference")),
    Tombstone(("eval", "judge-validity"), ("eval", "judge", "validate")),
    Tombstone(("eval", "judge-wording"), ("eval", "judge", "wording")),
    Tombstone(("eval", "cross-judge"), ("eval", "judge", "agreement")),
    Tombstone(("novelty", "analyze"), ("skill", "novelty", "analyze")),
    Tombstone(("novelty", "atoms"), ("skill", "novelty", "atoms")),
    Tombstone(("novelty", "probe"), ("skill", "novelty", "probe")),
    Tombstone(("novelty", "items"), ("skill", "novelty", "items")),
    Tombstone(("novelty", "shorten"), ("skill", "shorten")),
    Tombstone(("novelty", "routing"), ("skill", "routing"), rewrite=_novelty_routing_argv),
    Tombstone(("catalog", "scan"), ("catalog", "check")),
    Tombstone(("library", "intents"), ("catalog", "intents")),
    Tombstone(("library", "list"), ("catalog", "list"), ("--kind", "product")),
    Tombstone(("library", "show"), ("catalog", "show"), ("--kind", "product")),
    Tombstone(("product", "list"), ("catalog", "list"), ("--kind", "product")),
    Tombstone(("product", "show"), ("catalog", "show"), ("--kind", "product")),
    Tombstone(("subjects", "list"), ("catalog", "list"), ("--kind", "subject")),
    Tombstone(("subjects", "show"), ("catalog", "show"), ("--kind", "subject")),
    Tombstone(("repo", "tasks", "replay"), ("eval", "tasks", "generate"), ("--from", "replay")),
    Tombstone(("repo", "tasks", "authoring"), ("eval", "tasks", "generate"), ("--from", "prs")),
    Tombstone(("context", "trace"), ("trace", "inspect")),
    Tombstone(("context", "consistency"), ("trace", "consistency")),
    Tombstone(("context", "prefilter"), ("trace", "prefilter")),
    Tombstone(("dataset", "export"), ("data", "export")),
    Tombstone(("ingest", "docling"), ("data", "import", "docling")),
    Tombstone(("run",), ("docs", "check")),
    Tombstone(("compare",), ("docs", "compare")),
    # The retired roots: a bare `personas` or `questions` (or its help) points at `eval`.
    Tombstone(("personas",), ("eval", "personas")),
    Tombstone(("questions",), ("eval", "questions")),
    Tombstone(("context",), ("trace",)),
    Tombstone(("library",), ("catalog",)),
    Tombstone(("product",), ("catalog",)),
    Tombstone(("dataset",), ("data", "export")),
    Tombstone(("ingest",), ("data", "import")),
)

SCHEMA_VERSION = "cli_routes.v1"


def _leaf(path: tuple[str, ...]) -> Leaf | None:
    return next((leaf for leaf in LEAVES if leaf.path == path), None)


def cost_of(path: tuple[str, ...]) -> str | None:
    """``free`` or ``paid`` for a leaf or a root; None for a route the table does not know."""
    leaf = _leaf(path)
    if leaf is not None:
        return leaf.cost
    under = [leaf for leaf in LEAVES if leaf.path[: len(path)] == path]
    if not under:
        return None
    return PAID if any(leaf.cost == PAID for leaf in under) else FREE


def summary_of(path: tuple[str, ...]) -> str | None:
    """The table's fallback one-line help for a route whose parser has none."""
    leaf = _leaf(path)
    return leaf.summary if leaf is not None else None


def _canonical(path: tuple[str, ...]) -> tuple[str, ...]:
    """``path`` with its first word replaced by the root name when that word is an alias."""
    if not path:
        return path
    head = next((root.name for root in ROOTS if path[0] in (root.name, *root.aliases)), path[0])
    return (head, *path[1:])


def moved_argv(
    argv: list[str], tombstones: tuple[Tombstone, ...] | None = None
) -> list[str] | None:
    """The argv a tombstone rewrites ``argv`` to, or None when no tombstone matches its start.

    An alias typed on the command line (``product`` for ``library``) matches the root's tombstones.
    A path that is itself a live leaf under the old prefix is not a tombstone: ``eval score
    pointwise`` is live even though ``eval score`` moved there.
    """
    canon = _canonical(tuple(argv))
    live = {leaf.path for leaf in LEAVES}
    for tomb in TOMBSTONES if tombstones is None else tombstones:
        n = len(tomb.old)
        if canon[:n] != _canonical(tomb.old):
            continue
        if any(canon[:k] in live for k in range(n + 1, len(canon) + 1)):
            continue
        # A group that stays under its own name (`eval score` -> `eval score pointwise`) keeps its help.
        # A plain word after it is a mistyped leaf (argparse names the choices); an option is an old call.
        if tomb.new[:n] == _canonical(tomb.old) and (
            all(a in ("-h", "--help") for a in argv[n:]) or not argv[n].startswith("-")
        ):
            continue
        if tomb.rewrite is not None:
            return tomb.rewrite(list(argv[n:]))
        return [*tomb.new, *tomb.flags, *argv[n:]]
    return None


def moved_flags_argv(argv: Sequence[str]) -> list[str] | None:
    """``argv`` with each moved spelling rewritten to its new one, or None when none moved."""
    canon = _canonical(tuple(argv))
    leaf = max((leaf.path for leaf in LEAVES if canon[: len(leaf.path)] == leaf.path),
               key=len, default=())
    rewritten = list(argv)
    changed = False
    for index, token in enumerate(rewritten):
        if token == "--":  # the end of options: what follows is positional
            break
        name, equals, value = token.partition("=")
        for move in FLAG_MOVES:
            if name == move.old and (not move.leaves or leaf in move.leaves):
                rewritten[index] = f"{move.new}{equals}{value}"
                changed = True
                break
    return rewritten if changed else None


def moved_text(argv: list[str]) -> str:
    """The line a tombstone prints: the new command with the argv rewritten."""
    return "moved: " + shlex.join(["dokimi", *argv])


def _resolve(path: tuple[str, ...]) -> tuple[str, ...] | None:
    """``path`` with its first word resolved from an alias to its root; None if no root has it."""
    if not path:
        return None
    heads = {name: root.name for root in ROOTS for name in (root.name, *root.aliases)}
    head = heads.get(path[0])
    return None if head is None else (head, *path[1:])


def _is_route(path: tuple[str, ...]) -> bool:
    """True when argv ``path`` names a root or an alias, or a group or leaf under one.

    ``moved_argv`` matches by prefix and skips a live leaf, so a tombstone on a group shadows
    the argv under it that is not a live leaf.
    """
    full = _resolve(path)
    return full is not None and any(leaf.path[: len(full)] == full for leaf in LEAVES)


def _extends_a_leaf(path: tuple[str, ...]) -> bool:
    """True when ``path`` is longer than a live leaf it starts with: the leaf takes it as arguments."""
    full = _resolve(path)
    return full is not None and any(
        len(leaf.path) < len(full) and full[: len(leaf.path)] == leaf.path for leaf in LEAVES
    )


def tombstone_problems(tombstones: tuple[Tombstone, ...] | None = None) -> list[str]:
    """Why a tombstone is wrong: its old path is still a live route, or it points nowhere."""
    problems = []
    for tomb in TOMBSTONES if tombstones is None else tombstones:
        if not tomb.old or tomb.old[0].startswith("-"):  # an empty path matches every argv
            problems.append(f"{' '.join(tomb.old) or '(empty)'} is not a command path")
        # A group the move leaves in place (`eval score`) is a route, but not an old one.
        # Only a group of two or more words can stay in place; a whole root or alias cannot.
        stays = len(tomb.old) > 1 and tomb.new[: len(tomb.old)] == _canonical(tomb.old)
        if _is_route(tomb.old) and not stays:
            problems.append(f"{' '.join(tomb.old)} is still a live route")
        if _extends_a_leaf(tomb.old):
            problems.append(f"{' '.join(tomb.old)} extends a live route and would shadow it")
        if not _is_route(tomb.new):
            problems.append(
                f"{' '.join(tomb.old)} points at {' '.join(tomb.new)}, which is not a route"
            )
    return problems


def document() -> dict:
    """The table as JSON, for ``dokimi data schema show cli_routes``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": list(SECTIONS),
        "roots": [
            {
                "name": root.name,
                "section": root.section,
                "aliases": list(root.aliases),
                "cost": cost_of((root.name,)),
            }
            for root in ROOTS
        ],
        "leaves": [{"path": list(leaf.path), "cost": leaf.cost} for leaf in LEAVES],
        "tombstones": [
            {"old": list(tomb.old), "new": list(tomb.new), "flags": list(tomb.flags)} for tomb in TOMBSTONES
        ],
    }
