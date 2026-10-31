# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One flag vocabulary: each concept, its canonical spellings, types and banned synonyms.

This is the registry of issue #94 §2.4 (stage F1a). It is data only: no leaf reads it yet.
F1b moves the leaves of one concept at a time onto it, and tombstones the old spellings.
Until then the contract test in ``tests/test_cli_flags.py`` runs in ratchet mode: a
spelling that is neither canonical here nor on ``tests/data/cli_flags_allowlist.txt``
fails the build, and so does a banned synonym whose concept has a canonical spelling,
unless that spelling is on the allowlist. The check looks at spellings only: an option's
type and action are not compared with ``kind``.

A concept is one row of the §2.4 audit. Its ``canonical`` spellings are the ones new
commands use. ``banned`` lists the synonyms the audit found for it. A banned spelling
with an empty ``canonical`` tuple is not enforced: the concept has no decided spelling
yet.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "CONCEPTS",
    "OWNED_STEMS",
    "Concept",
    "Flag",
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
                 meaning="the judge; repeated, it is a panel"),
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
            Flag("--max-output-tokens", "N", "int"),
            Flag("--retrieval-tokens", "N", "int"),
            Flag("--token-budgets", "F,F", "text"),
        ),
        banned=("--max-tokens", "--budgets"),
        note="--max-tokens meant retrieval in six leaves and output in seven.",
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
    ),
    Concept(
        name="run_name",
        help="the identity of a run directory",
        canonical=(Flag("--name", "NAME", "text"),),
        banned=("--run-id", "--run-ids", "--batch"),
    ),
    Concept(
        name="network",
        help="network policy, per phase",
        canonical=(Flag("--network", "[PHASE=]MODE[:hosts]", "text"),),
        banned=("--agent-network", "--build-network"),
    ),
    Concept(
        name="timeout",
        help="time limits, per phase",
        canonical=(Flag("--timeout", "[PHASE=]SEC", "text"),),
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
        ),
        banned=("--product", "--library", "--libraries", "--target", "--root", "--repo-root",
                "--tasks-root", "--subjects-root", "--questions-root", "--questions-from",
                "--custom-questions", "--atoms", "--novelty-artifact", "--from-eval"),
        note="The subject is a positional. --product, --library and --target name it, so new "
             "commands do not use them.",
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
            Flag("--correction", "NAME", "text"),
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
