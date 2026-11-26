# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One flag vocabulary: each concept, its canonical spellings, types and banned synonyms.

This is the registry of issue #94 §2.4 (stage F1a). A leaf adds a registry flag with ``add``;
``FLAG_MOVES`` tombstones the old spellings of a concept that moved (F1b, one concept per PR).
The contract test in ``tests/test_cli_flags.py`` runs in ratchet mode: a
spelling that is neither canonical here nor on ``tests/data/cli_flags_allowlist.txt``
fails the build, and so does a banned synonym whose concept has a canonical spelling,
unless that spelling is on the allowlist. The same test also compares every use of a canonical
spelling with its registry row: switch or value (``kind``), metavar (``value``), choices and
type (``int``, ``float`` and ``usd`` rows). ``meaning_clashes`` in ``tests/test_cli_flags.py`` says what is compared; a
flag's repeatability is not compared.

A concept is one row of the §2.4 audit. Its ``canonical`` spellings are the ones new
commands use. ``banned`` lists the synonyms the audit found for it. A banned spelling
with an empty ``canonical`` tuple is not enforced: the concept has no decided spelling
yet.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "CONCEPTS",
    "FLAG_MOVES",
    "OWNED_STEMS",
    "Concept",
    "Flag",
    "FlagMove",
    "JoinedValues",
    "add",
    "registry_problems",
]


@dataclass(frozen=True)
class Flag:
    """One canonical spelling of a concept.

    ``value`` is the metavar shown in help (``""`` for a boolean flag), ``kind`` the type
    the flag takes (``path``, ``model_ref``, ``int``, ``float``, ``usd``, ``choice``,
    ``flag`` or ``text``), ``repeatable`` whether it may be given more than once.
    """

    spelling: str
    value: str = ""
    kind: str = "text"
    repeatable: bool = False
    meaning: str = ""


@dataclass(frozen=True)
class Concept:
    """A concept with one meaning across the CLI, its spellings and its banned synonyms.

    ``api_kwarg``, ``env`` and ``plan_field`` name the same concept in ``dokimi.api``, the
    environment and ``plan.v1``. Only ``spend`` has them so far: §2.4 names them there.
    """

    name: str
    help: str
    canonical: tuple[Flag, ...]
    banned: tuple[str, ...] = ()
    api_kwarg: str | None = None
    env: str | None = None
    plan_field: str | None = None
    note: str = ""


_MODEL_REF = "model_ref"

CONCEPTS: tuple[Concept, ...] = (
    Concept(
        name="model",
        help="the model under test",
        canonical=(Flag("--model", "PROVIDER/MODEL", _MODEL_REF,
                        meaning="the model under test; repeatable where several are allowed"),),
        banned=("--provider", "--work-model"),
    ),
    Concept(
        name="role_models",
        help="the model a role runs on: judge, extractor, escalation, fill, RAGAS",
        canonical=(
            Flag("--judge", "PROVIDER/MODEL", _MODEL_REF, repeatable=True,
                 meaning="the judge; repeated, it is a panel on the commands that take several (novelty, catalog, "
                         "eval judge agreement); elsewhere a repeat replaces the earlier one"),
            Flag("--judge-aggregate", "{median,majority,unanimous}", "choice",
                 meaning="how a judge panel's verdicts become one"),
            Flag("--extract-model", "PROVIDER/MODEL", _MODEL_REF),
            Flag("--escalate-model", "PROVIDER/MODEL", _MODEL_REF),
            Flag("--fill-model", "PROVIDER/MODEL", _MODEL_REF),
            Flag("--ragas-model", "PROVIDER/MODEL", _MODEL_REF),
        ),
        banned=("--judge-model", "--judge-provider", "--judges", "--judge-panel",
                "--judge-configs", "--extract-provider"),
        note="A role the audit does not list uses --ROLE-model P/M. The --escalate-*, --fill-* "
             "and --ragas-* spellings are not enumerated in the audit; F1b lists them.",
    ),
    Concept(
        name="sampling",
        help="temperature and seed",
        canonical=(
            Flag("--temperature", "FLOAT", "float"),
            Flag("--seed", "N", "int", meaning="the resolved seed is recorded in every run"),
        ),
        note="A role's temperature uses --ROLE-temperature.",
    ),
    Concept(
        name="arm",
        help="a variant of the subject",
        canonical=(Flag("--arm", "[NAME=]REF", "text", repeatable=True,
                        meaning="a variant; docs:SOURCE for a documentation arm"),),
        banned=("--arms",),
    ),
    Concept(
        name="context-framing",
        help="the context preamble a novelty measurement puts before the artifact",
        canonical=(Flag("--context-framing", "reference|neutral", "choice",
                        meaning="neutral says the text may be irrelevant (the placebo-abstention control)"),),
    ),
    Concept(
        name="cell",
        help="one cell of a matrix",
        canonical=(Flag("--cell", "k=v,...", "text", repeatable=True),),
        banned=("--matrix-cell", "--matrix-cells"),
    ),
    Concept(
        name="baseline",
        help="the run or arm a comparison is made against",
        canonical=(
            Flag("--baseline", "RUN|baseline:NAME|FILE", "text"),
            Flag("--baseline-arm", "NAME", "text"),
        ),
        banned=("--baseline-json", "--plugin-json", "--base"),
    ),
    Concept(
        name="harness",
        help="the coding harness, and its executable",
        canonical=(
            Flag("--harness", "NAME", "text", repeatable=True),
            Flag("--bin", "NAME=PATH", "path", repeatable=True),
        ),
        banned=("--harnesses", "--work-harnesses"),
    ),
    Concept(
        name="reps",
        help="repetitions per cell",
        canonical=(Flag("--reps", "N", "int"),),
        banned=("--repeats", "--work-repeats", "--multi-run", "--trials"),
    ),
    Concept(
        name="against",
        help="the root of the skills a skill is measured against",
        canonical=(Flag("--against", "ROOT", "path"),),
        banned=(),
    ),
    Concept(
        name="concurrency",
        help="how much runs at once",
        canonical=(
            Flag("--concurrency", "N", "int"),
            Flag("-j", "N", "int", meaning="short form of --concurrency"),
        ),
        banned=("--jobs",),
    ),
    Concept(
        name="spend",
        help="caps on paid calls, in USD",
        canonical=(
            Flag("--max-usd", "X", "usd", meaning="the cap on the whole command"),
            Flag("--max-usd-per-cell", "X", "usd"),
            Flag("--max-cells", "N", "int"),
            Flag("--budget-ledger", "FILE", "path", meaning="one ledger shared across commands"),
        ),
        banned=("--max-cost-usd", "--max-batch-usd", "--budget"),
        api_kwarg="max_usd",
        env="DOKIMI_MAX_USD",
        plan_field="budget.max_usd",
        note="A CLI cap can lower the plan cap and never raise it.",
    ),
    Concept(
        name="dry_run",
        help="estimate without spending",
        canonical=(Flag("--dry-run", kind="flag", meaning="estimate, make no paid call"),),
        banned=("--estimate-only", "--work-dry-run"),
    ),
    Concept(
        name="tokens",
        help="token caps and token budget fractions",
        canonical=(
            Flag("--max-output-tokens", "N", "int", meaning="the output ceiling of each model call"),
            Flag("--retrieval-tokens", "N", "int", meaning="the tokens of docs retrieved per question"),
            Flag("--token-budgets", "F [F ...]", "float",
                 meaning="target token fractions for shortening candidates"),
        ),
        banned=("--max-tokens", "--budgets"),
        note="--max-tokens meant retrieval in four leaves and output in ten; FLAG_MOVES moves each leaf "
             "by what its code does.",
    ),
    Concept(
        name="output",
        help="where results go, and the machine-readable form",
        canonical=(
            Flag("--out", "FILE", "path", meaning="the primary artifact, always a file"),
            Flag("--out-dir", "DIR", "path", meaning="the run directory"),
            Flag("--candidate-dir", "DIR", "path", meaning="a written skill or patch"),
            Flag("--json", kind="flag", meaning="cli_output.v1 on stdout"),
            Flag("--format", "FORMAT", "choice", meaning="a render format only"),
        ),
        banned=("--output", "--out-json", "--out-md", "--output-dir", "--results-dir",
                "--items-out", "--no-json", "--view-format"),
    ),
    Concept(
        name="fail_on",
        help="exit non-zero on warnings or failures",
        canonical=(Flag("--fail-on", "{warn,fail}", "choice"),),
        banned=("--strict", "--fail-on-warning", "--fail-on-error", "--fail-on-fail"),
        note="A leaf takes the values it can gate on: warn, fail, or both. --strict stays where it "
             "gates a verdict (an exit code of its own), not a finding level; the allowlist says why.",
    ),
    Concept(
        name="run_name",
        help="the identity of a run directory",
        canonical=(Flag("--name", "NAME", "text"),),
        banned=("--run-id", "--run-ids", "--batch"),
    ),
    Concept(
        name="retention",
        help="which runs housekeeping removes and which it keeps",
        canonical=(
            Flag("--older-than", "AGE", "text", meaning="runs whose newest artifact is older than AGE (30d)"),
            Flag("--keep-baselines", kind="flag", meaning="keep the runs a saved baseline holds"),
        ),
    ),
    Concept(
        name="network",
        help="network policy, per phase",
        canonical=(Flag("--network", "[PHASE=]MODE[:hosts]", "text"),),
        banned=("--agent-network", "--build-network"),
    ),
    Concept(
        name="suite",
        help="a suite manifest: tasks whose base is in another repository (contract §2.1)",
        canonical=(Flag("--suite", "PATH", "text", meaning="the suite file, in the eval tree"),
                   Flag("--allow-floating-suite-revs", kind="flag",
                        meaning="resolve a branch or tag of a suite repository when the run starts")),
    ),
    Concept(
        name="online",
        help="network checks of a catalog's skills, opt in",
        canonical=(Flag("--online", kind="flag", meaning="also check packages and links over the network"),),
        banned=(),
    ),
    Concept(
        name="timeout",
        help="time limits, per phase",
        # F1b adds the phase prefix (#94 §2.4). Current repo leaves parse whole seconds.
        canonical=(Flag("--timeout", "[PHASE=]SEC", "int"),),
        banned=("--grade-timeout", "--build-timeout"),
    ),
    Concept(
        name="cache",
        help="caches, shared or per name",
        canonical=(
            Flag("--cache-dir", "DIR", "path"),
            Flag("--cache", "NAME=DIR", "text", repeatable=True),
        ),
        banned=("--cache-root",),
    ),
    Concept(
        name="environment",
        help="the working directory and how the agent is delegated to",
        canonical=(
            Flag("--work-dir", "DIR", "path"),
            Flag("--delegation", "native|none", "choice"),
        ),
    ),
    Concept(
        name="inputs",
        help="what a run reads; the subject itself is a positional",
        canonical=(
            Flag("--tasks", "DIR|FILE", "path"),
            Flag("--task", "ID", "text", repeatable=True, meaning="a filter on --tasks"),
            Flag("--questions", "FILE", "path"),
            Flag("--answers", "FILE", "path"),
            Flag("--registry", "FILE", "path"),
            Flag("--from", "RUN", "text"),
            Flag("--ledger", "FILE", "path"),
            Flag("--index", "FILE", "path", meaning="a SCIP code index of the tree"),
        ),
        banned=("--product", "--library", "--libraries", "--target", "--root", "--repo-root",
                "--tasks-root", "--subjects-root", "--questions-root", "--questions-from",
                "--custom-questions", "--atoms", "--novelty-artifact", "--from-eval"),
        note="The subject is a positional. --product, --library and --target name it, so new "
             "commands do not use them.",
    ),
    Concept(
        name="upstream",
        help="the upstream copies a skill is matched against (catalog provenance)",
        canonical=(
            Flag("--upstream", "CHECKOUT", "path", repeatable=True,
                 meaning="a local git checkout of an upstream; its history gives the versions"),
            Flag("--upstream-manifest", "FILE", "path", repeatable=True,
                 meaning="a dokimi.upstream.v1 JSON manifest of one upstream"),
        ),
    ),
    Concept(
        name="catalog_impact",
        help="the local base revision for catalog change selection",
        canonical=(Flag("--changed-from", "REF", "text"),),
    ),
    Concept(
        name="retrieval",
        help="documentation retrieval",
        canonical=(
            Flag("--top-k", "N", "int"),
            Flag("--rerank-threshold", "X", "float"),
        ),
        banned=("--doc-source", "--context7-id"),
        note="A documentation source is an arm: --arm docs:SOURCE.",
    ),
    Concept(
        name="api_header",
        help="a C header whose declared names a veto may check",
        canonical=(Flag("--header", "PATH", "path", repeatable=True,
                        meaning="a C header of the API (glob ok)"),),
        note="Used with --prefix (and --shared-lib) by skill novelty ground.",
    ),
    Concept(
        name="shared_library",
        help="a shared library whose exported symbols a veto may check",
        canonical=(Flag("--shared-lib", "PATH", "path", repeatable=True,
                        meaning="a shared library whose exported (nm -D) names are known"),),
        note="Used with --prefix (and --header) by skill novelty ground.",
    ),
    Concept(
        name="symbol_prefix",
        help="the C identifier prefix that scopes the names a veto checks",
        canonical=(Flag("--prefix", "PREFIX", "text", repeatable=True,
                        meaning="a C identifier prefix ending in '_' (cblas_)"),),
    ),
    Concept(
        name="force",
        help="re-run over existing output",
        canonical=(Flag("--force", kind="flag"),),
        banned=("--force-regen",),
    ),
    Concept(
        name="policy",
        help="the gate policy and the statistics of a comparison",
        canonical=(
            Flag("--policy", "FILE|NAME", "text"),
            Flag("--method", "NAME", "text"),
            Flag("--unit", "NAME", "text"),
            Flag("--contrast", "NAME", "text"),
            Flag("--correction", "{holm,bh}", "choice",
                 meaning="the multiple-comparison correction across arms or runs: holm (family-wise) or bh (FDR)"),
        ),
        banned=("--stats-correction",),
    ),
)

# Stems that only the registry may use for a local flag. F1c enforces this; F1a only lists them.
OWNED_STEMS: tuple[str, ...] = (
    "out", "model", "judge", "usd", "budget", "token", "reps", "json", "format", "strict",
)


def registry_problems(concepts: Iterable[Concept] = CONCEPTS) -> list[str]:
    """Self-check of the registry data: empty when it is consistent."""
    concepts = tuple(concepts)
    problems: list[str] = []
    canonical_owner: dict[str, str] = {}
    banned_owner: dict[str, str] = {}
    for concept in concepts:
        for flag in concept.canonical:
            if not flag.spelling.startswith("-"):
                problems.append(f"{concept.name}: canonical `{flag.spelling}` is not an option")
            if flag.spelling in canonical_owner:
                problems.append(f"`{flag.spelling}` is canonical in both "
                                f"{canonical_owner[flag.spelling]} and {concept.name}")
            canonical_owner[flag.spelling] = concept.name
    for concept in concepts:
        for spelling in concept.banned:
            banned_owner.setdefault(spelling, concept.name)
            if not spelling.startswith("--"):
                problems.append(f"{concept.name}: banned `{spelling}` is not a long option")
            if spelling in canonical_owner:
                problems.append(f"`{spelling}` is canonical in {canonical_owner[spelling]} and "
                                f"banned in {concept.name}")
            if banned_owner[spelling] != concept.name:
                problems.append(f"`{spelling}` is banned in both {banned_owner[spelling]} and "
                                f"{concept.name}")
    return problems


class JoinedValues(argparse.Action):
    """A repeatable flag whose values join with commas: ``--arm a --arm b,c`` is ``a,b,c``."""

    def __call__(self, parser, namespace, values, option_string=None):
        prior = getattr(namespace, self.dest, None)
        setattr(namespace, self.dest, values if prior is None else f"{prior},{values}")


def add(parser: argparse.ArgumentParser, concept: str, *args: str, **kwargs: object) -> argparse.Action:
    """Add a flag of ``concept`` to ``parser``. Every option string must be a canonical spelling of it."""
    spellings = {flag.spelling for c in CONCEPTS if c.name == concept for flag in c.canonical}
    if not spellings:
        raise KeyError(f"no concept named {concept!r} in the flag registry")
    for option in args:
        if option.startswith("-") and option not in spellings:
            raise ValueError(f"`{option}` is not a canonical spelling of `{concept}`")
    return parser.add_argument(*args, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class FlagMove:
    """An old spelling that moved: argv that uses it is tombstoned (exit 2, prints the new argv).

    ``leaves`` limits the move to those leaves (their argv paths), for a spelling that keeps
    another meaning on other leaves. The empty tuple means every leaf.
    """

    old: str
    new: str
    leaves: tuple[tuple[str, ...], ...] = ()
    split: str = ""  # a value that held several items (``--judges a,b``) becomes one ``new`` per item


@dataclass(frozen=True)
class FlagJoin:
    """Two old flags that became one value: ``provider`` and ``model`` are now ``new PROVIDER/MODEL``.

    A provider with no model gets the placeholder ``<MODEL>`` in the printed argv. ``leaves`` limits
    the join as in ``FlagMove``; the empty tuple means every leaf.
    """

    provider: str
    model: str
    new: str
    leaves: tuple[tuple[str, ...], ...] = ()


# Leaves whose judge was ``--judge-model``/``--judge-provider``; now ``--judge PROVIDER/MODEL`` (issue #94 F1b).
# arms run, arms matrix-run, subjects run and novelty keep both spellings: there ``--judge`` is a switch or a panel.
JUDGE_MOVED_LEAVES: tuple[tuple[str, ...], ...] = (
    ("benchmark", "run"), ("benchmark", "preflight"), ("benchmark", "batch"),
    ("eval", "judge", "validate"), ("eval", "judge", "wording"),
    ("eval", "score", "checklist"), ("eval", "score", "pairwise"),
    ("eval", "score", "pointwise"), ("eval", "score", "reference"),
    ("evaluate",),
)


FLAG_JOINS: tuple[FlagJoin, ...] = (
    # Model concept (F1b): the provider moved into the model value, `--model PROVIDER/MODEL`.
    FlagJoin("--provider", "--model", "--model"),
    FlagJoin("--judge-provider", "--judge-model", "--judge", leaves=JUDGE_MOVED_LEAVES),
    FlagJoin("--extract-provider", "--extract-model", "--extract-model", leaves=(
        ("catalog", "novelty"), ("skill", "novelty", "analyze"), ("skill", "novelty", "probe"),
    )),
)


FLAG_MOVES: tuple[FlagMove, ...] = (
    # Output concept (F1b): every directory is `--out-dir`, every JSON artifact is `--out`.
    FlagMove("--output-dir", "--out-dir"),
    FlagMove("--out-json", "--out"),
    # `--out` names a file everywhere else, so the directory leaves move on their own.
    FlagMove("--out", "--out-dir", leaves=(
        ("repo", "run"), ("repo", "reach"), ("repo", "skill-reach"),
        ("eval", "check"), ("eval", "tasks", "generate"),
    )),
    # Tokens concept (F1b): `--max-tokens` was retrieval on four leaves and the output ceiling on ten.
    FlagMove("--max-tokens", "--retrieval-tokens", leaves=(
        ("answers", "generate"), ("benchmark", "run"), ("benchmark", "preflight"), ("benchmark", "batch"),
    )),
    FlagMove("--max-tokens", "--max-output-tokens", leaves=(
        ("arms", "run"), ("arms", "matrix-run"), ("subjects", "run"), ("tools", "preference"),
        ("skill", "novelty", "analyze"), ("skill", "novelty", "atoms"),
        ("skill", "novelty", "probe"), ("skill", "shorten"),
        ("skill", "routing"), ("catalog", "routing"), ("catalog", "novelty"),
    )),
    FlagMove("--budgets", "--token-budgets", leaves=(
        ("skill", "novelty", "analyze"), ("skill", "novelty", "atoms"),
        ("skill", "novelty", "probe"), ("skill", "shorten"), ("catalog", "novelty"),
    )),
    # Reps concept (F1b): each old spelling counted the same thing on its one leaf, so the move
    # is limited to that leaf (elsewhere the hint would name a flag the command does not take).
    FlagMove("--repeats", "--reps", leaves=(("tasks", "run"),)),
    FlagMove("--work-repeats", "--reps", leaves=(("subjects", "run"),)),
    FlagMove("--multi-run", "--reps", leaves=(("benchmark", "run"),)),
    FlagMove("--trials", "--reps", leaves=(("catalog", "trigger-probe"),)),
    # Concurrency concept (F1b): how much runs at once; `-j` stays as its short form. The old
    # `--jobs` was on these three leaves only.
    FlagMove("--jobs", "--concurrency", leaves=(("catalog", "check"), ("eval", "check"), ("repo", "run"))),
    # Fail-on concept (F1b): a gate on warnings or failures is `--fail-on {warn,fail}`. The moved
    # leaves gate on the level their old flag gated on; `--strict` keeps its other meanings.
    FlagMove("--fail-on-warning", "--fail-on=warn", leaves=(("eval", "check"), ("repo", "budget"))),
    FlagMove("--fail-on-fail", "--fail-on=fail", leaves=(("repo", "scan"),)),
    FlagMove("--fail-on-error", "--fail-on=fail", leaves=(("repo", "inspect"),)),
    FlagMove("--strict", "--fail-on=warn", leaves=(("eval", "triggers", "generate"),)),
    # Run-name concept (F1b): the identity of a run directory. `--batch` is the batch directory's name on repo run.
    FlagMove("--run-id", "--name", leaves=(("answers", "generate"), ("eval", "score", "pointwise"), ("report", "eval"))),
    FlagMove("--batch", "--name", leaves=(("repo", "run"),)),
    # Force concept (F1b): re-run over existing output. benchmark run and batch discard the cached personas and questions.
    FlagMove("--force-regen", "--force", leaves=(("benchmark", "run"), ("benchmark", "batch"))),
    # Policy concept (F1b): the statistics of a comparison; `--correction` takes holm or bh.
    FlagMove("--stats-correction", "--correction", leaves=(
        ("arms", "run"), ("arms", "matrix-run"), ("report", "model-compare"),
    )),
    # Spend concept (F1b): a whole-command cap is --max-usd, a cap per agent run is --max-usd-per-cell.
    FlagMove("--max-cost-usd", "--max-usd", leaves=(("tools", "preference"),)),
    FlagMove("--max-batch-usd", "--max-usd", leaves=(("repo", "run"),)),
    FlagMove("--budget", "--max-usd-per-cell", leaves=(
        ("repo", "run"), ("repo", "reach"), ("repo", "skill-reach"),
    )),
    # Model concept (F1b): a bare judge model moves to `--judge` (the provider is in the value).
    FlagMove("--judge-model", "--judge", leaves=JUDGE_MOVED_LEAVES),
    FlagMove("--judges", "--judge", leaves=(("eval", "judge", "agreement"),), split=","),
    # Dry-run concept (F1b): estimate, make no paid call. `subjects run --work-dry-run` is not moved:
    # it gates only executable-task agents, so it has no matching canonical meaning (see the allowlist).
    FlagMove("--estimate-only", "--dry-run", leaves=(
        ("catalog", "routing"), ("skill", "routing"), ("skill", "novelty", "analyze"),
    )),
    # Arm concept (F1b): a subject arm list is repeatable --arm; the old comma list is the same value.
    FlagMove("--arms", "--arm", leaves=(("arms", "run"), ("arms", "matrix-run"), ("explore", "run"))),
    # Harness concept (F1b): the list of harnesses a tasks run uses, repeatable or comma-separated.
    FlagMove("--harnesses", "--harness", leaves=(("tasks", "run"),)),
    # Baseline concept (F1b): the artifact or snapshot a comparison is made against.
    FlagMove("--baseline-json", "--baseline", leaves=(("arms", "plugin-delta"),)),
    FlagMove("--base", "--baseline", leaves=(("docs", "compare"),)),
)

