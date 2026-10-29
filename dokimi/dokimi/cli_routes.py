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
from dataclasses import dataclass

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
    """An operation: a parser with no subcommands. ``path`` is its argv, e.g. ``("eval", "score")``."""

    path: tuple[str, ...]
    cost: str
    summary: str | None = None  # one-line help, used only when the parser has none


@dataclass(frozen=True)
class Tombstone:
    """An old argv prefix that now moved: it answers with ``new`` and exits 2, never forwards."""

    old: tuple[str, ...]
    new: tuple[str, ...]


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
    Root("run", "Subjects"),
    Root("compare", "Subjects"),
    Root("library", "Subjects", aliases=("product",)),
    Root("catalog", "Subjects"),
    Root("novelty", "Subjects"),
    Root("repo", "Subjects"),
    Root("personas", "Evals"),
    Root("questions", "Evals"),
    Root("eval", "Evals"),
    Root("dataset", "Analysis and data"),
    Root("ingest", "Analysis and data"),
    Root("tools", "Analysis and data"),
    Root("context", "Analysis and data"),
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
    Leaf(("subjects", "list"), FREE),
    Leaf(("subjects", "show"), FREE),
    Leaf(("subjects", "run"), PAID),
    Leaf(("tasks", "list"), FREE),
    Leaf(("tasks", "inspect"), FREE),
    Leaf(("tasks", "harnesses"), FREE),
    Leaf(("tasks", "run"), PAID),
    Leaf(("explore", "list"), FREE),
    Leaf(("explore", "run"), PAID),
    Leaf(("run",), FREE, "Run the static benchmark spec and save a snapshot"),
    Leaf(("compare",), FREE, "Compare two saved benchmark snapshots"),
    Leaf(("library", "list"), FREE),
    Leaf(("library", "show"), FREE),
    Leaf(("library", "intents"), FREE),
    Leaf(("catalog", "scan"), FREE),
    Leaf(("catalog", "formats"), FREE),
    Leaf(("catalog", "trigger-set"), PAID),
    Leaf(("catalog", "trigger-probe"), PAID),
    Leaf(("novelty", "analyze"), PAID),
    Leaf(("novelty", "atoms"), PAID),
    Leaf(("novelty", "probe"), PAID),
    Leaf(("novelty", "shorten"), PAID),
    Leaf(("novelty", "routing"), PAID),
    Leaf(("novelty", "items"), FREE),
    Leaf(("novelty", "view"), FREE),
    Leaf(("repo", "validate"), FREE),
    Leaf(("repo", "scan"), FREE),
    Leaf(("repo", "inspect"), FREE),
    Leaf(("repo", "fetch"), FREE),
    Leaf(("repo", "reach"), PAID),
    Leaf(("repo", "budget"), FREE),
    Leaf(("repo", "tasks", "check"), FREE),
    Leaf(("repo", "run"), PAID),
    Leaf(("repo", "report"), FREE),
    Leaf(("repo", "compare"), FREE),
    Leaf(("personas", "discover"), PAID),
    Leaf(("personas", "approve"), FREE),
    Leaf(("questions", "generate"), PAID),
    Leaf(("questions", "analyze"), PAID),
    Leaf(("questions", "refine"), PAID),
    Leaf(("questions", "panel-review"), PAID),
    Leaf(("questions", "import"), FREE),
    Leaf(("eval", "score"), PAID),
    Leaf(("eval", "ragas"), PAID),
    Leaf(("eval", "grounding"), FREE),
    Leaf(("eval", "judge-validity"), PAID),
    Leaf(("eval", "checklist"), PAID),
    Leaf(("eval", "judge-wording"), PAID),
    Leaf(("eval", "cross-judge"), PAID),
    Leaf(("eval", "pairwise"), PAID),
    Leaf(("eval", "reference"), PAID),
    Leaf(("eval", "native-import"), FREE),
    Leaf(("eval", "panel-score"), PAID),
    Leaf(("dataset", "export"), FREE),
    Leaf(("ingest", "docling"), FREE),
    Leaf(("tools", "preference"), PAID),
    Leaf(("context", "trace"), FREE),
    Leaf(("data", "schema", "list"), FREE),
    Leaf(("data", "schema", "show"), FREE),
)

# No command has moved yet: the renames of #94 §5 M1-M3 add entries here.
TOMBSTONES: tuple[Tombstone, ...] = ()

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


def moved_argv(
    argv: list[str], tombstones: tuple[Tombstone, ...] | None = None
) -> list[str] | None:
    """The argv a tombstone rewrites ``argv`` to, or None when no tombstone matches its start."""
    for tomb in TOMBSTONES if tombstones is None else tombstones:
        if tuple(argv[: len(tomb.old)]) == tomb.old:
            return [*tomb.new, *argv[len(tomb.old) :]]
    return None


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

    ``moved_argv`` matches by prefix, so a tombstone on a group shadows every route under it.
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
        if _is_route(tomb.old):
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
        "tombstones": [{"old": list(tomb.old), "new": list(tomb.new)} for tomb in TOMBSTONES],
    }
