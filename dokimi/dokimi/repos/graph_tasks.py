# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Enumeration tasks with gold read from a SCIP code index (contract §4.7): ``eval tasks generate --from graph``.

Two questions, both graded by ``dokimi:graph_enum`` (§4.5):

- callers: every file (or, on an index with enclosing ranges, every function) that references one function,
  outside the files that define it. A reference is any non-definition occurrence: SCIP does not tell a call
  from another use, so the prompt says "references".
- derives: every class that derives directly from one class, from the index's base-class relations.

A target is a function or class defined in a tree file that is neither a test nor generated, with a gold list
of a size the options allow; dunder methods and C++ operators are never targets. No model, no agent, no
docker: the gold is what the index says, so a task is as right as the index it came from.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi.repos.code_index import CodeIndex
from dokimi.repos.code_index.symbols import descriptor_names
from dokimi.repos.environment.files import is_generated, is_test_file
from dokimi.repos.environment.structure import detect_prefix, function_like, is_protocol_name

GOLD = "gold.json"
GRADER = "dokimi:graph_enum"
ID_MAX = 80


@dataclass(frozen=True)
class Options:
    out: Path
    base: str  # the full SHA of the revision the index was built from
    callers: int = 9
    derives: int = 3
    min_gold: int = 5
    max_gold: int = 40
    min_subclasses: int = 2
    seed: int = 7
    max_turns: int = 40
    force: bool = False  # rewrite a task directory that already has a task.json


@dataclass(frozen=True)
class Candidate:
    kind: str  # callers | derives
    symbol: str
    name: str
    qualified: str
    defined_in: tuple[str, ...]
    gold: tuple[str, ...]
    same_name: int  # symbols of this kind with this name; above 1 the prompt says which one counts

    @property
    def ambiguous(self) -> bool:
        return self.same_name > 1


@dataclass
class Outcome:
    task: str
    status: str  # written | kept
    candidate: Candidate


@dataclass
class Plan:
    """What one index gave: the path mapping, the candidates of each kind and the tasks chosen from them."""

    granularity: str  # files | functions
    root_prefix: str
    documents: int
    documents_in_tree: int
    candidates: dict[str, int] = field(default_factory=dict)
    outcomes: list[Outcome] = field(default_factory=list)


def plan(index: CodeIndex, tree_files: list[str], opts: Options) -> Plan:
    """Map the index onto the tree, list the candidates and choose the tasks; nothing is written."""
    documents = index.files()
    prefix, _ = detect_prefix(documents, tree_files)
    in_tree = set(tree_files)
    repo_path = {doc: prefix + doc for doc in documents if prefix + doc in in_tree}
    granularity = "functions" if index.has_enclosing_ranges else "files"
    sep = "::" if index.tool.startswith("scip-clang") else "."
    callers = _callers(index, repo_path, granularity, sep, opts)
    derives = _derives(index, repo_path, sep, opts)
    rng = random.Random(opts.seed)
    used: set[str] = set()
    chosen = _choose(callers, opts.callers, rng, used) + _choose(derives, opts.derives, rng, used)
    out = Plan(granularity, prefix, len(documents), len(repo_path), {"callers": len(callers), "derives": len(derives)})
    out.outcomes = [Outcome(task_id(c), "kept" if (opts.out / task_id(c) / "task.json").exists() and not opts.force
                            else "written", c) for c in chosen]
    return out


def write(p: Plan, opts: Options, tool: str) -> None:
    """Write each chosen task that is not kept: ``task.json`` and ``gold.json`` in ``<out>/<task id>/``."""
    for o in p.outcomes:
        if o.status != "written":
            continue
        task, gold = _task(o.candidate, p.granularity, opts, tool)
        dest = opts.out / o.task
        dest.mkdir(parents=True, exist_ok=True)
        (dest / GOLD).write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (dest / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def task_id(c: Candidate) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", c.name.lower()).strip("_") or "symbol"
    return f"graph_{c.kind}_{slug}"[:ID_MAX]


def _target_files(index: CodeIndex, symbol: str, repo_path: dict[str, str]) -> tuple[str, ...] | None:
    """The tree files that define ``symbol``, or None when one is outside the tree, or all are tests or generated."""
    files = {repo_path.get(occ.path) for occ in index.definitions(symbol)}
    if not files or None in files:
        return None
    if all(is_test_file(f) or is_generated(f) for f in files):  # type: ignore[arg-type]
        return None
    return tuple(sorted(files))  # type: ignore[arg-type]


def _name(symbol: str, sep: str) -> tuple[str, str] | None:
    names = descriptor_names(symbol)
    if not names or is_protocol_name(names[-1]):
        return None
    return names[-1], sep.join(names)


def _callers(index: CodeIndex, repo_path: dict[str, str], granularity: str, sep: str,
             opts: Options) -> list[Candidate]:
    functions = [s for s in index.symbols() if function_like(s)]
    same: dict[str, int] = {}
    for s in functions:
        named = _name(s, sep)
        if named:
            same[named[0]] = same.get(named[0], 0) + 1
    scopes = _function_scopes(index) if granularity == "functions" else {}
    out = []
    for s in functions:
        named = _name(s, sep)
        defined = _target_files(index, s, repo_path) if named else None
        if named is None or defined is None:
            continue
        gold = _referrers(index, s, repo_path, defined, scopes if granularity == "functions" else None)
        if opts.min_gold <= len(gold) <= opts.max_gold:
            out.append(Candidate("callers", s, named[0], named[1], defined, tuple(sorted(gold)), same[named[0]]))
    return out


def _referrers(index: CodeIndex, symbol: str, repo_path: dict[str, str], defined: tuple[str, ...],
               scopes: dict[str, list[tuple[int, int, str]]] | None) -> set[str]:
    """The files (``scopes`` None) or ``path::function`` items that reference ``symbol`` outside the files that
    define it; empty when a reference lies outside the tree, since the gold would be incomplete."""
    gold: set[str] = set()
    for occ in index.references(symbol):
        path = repo_path.get(occ.path)
        if path is None:
            return set()
        if path in defined:
            continue
        if scopes is None:
            gold.add(path)
            continue
        fn = _innermost(scopes.get(occ.path, ()), occ.line)
        if fn is not None:  # module-level code and class bodies are not a function
            gold.add(f"{path}::{fn}")
    return gold


def _function_scopes(index: CodeIndex) -> dict[str, list[tuple[int, int, str]]]:
    """Per document, the line span and name of each function definition that has an enclosing range."""
    scopes: dict[str, list[tuple[int, int, str]]] = {}
    for doc in index.files():
        for occ in index.occurrences(doc):
            if occ.is_definition and occ.enclosing is not None and function_like(occ.symbol):
                names = descriptor_names(occ.symbol)
                if names:
                    scopes.setdefault(doc, []).append((*occ.enclosing, names[-1]))
    return scopes


def _innermost(scopes: Any, line: int) -> str | None:
    """The function whose span holds ``line`` and starts last (a nested function is inside its parent)."""
    best: tuple[int, int, str] | None = None
    for first, last, name in scopes:
        if first <= line <= last and (best is None or (first, -last) > (best[0], -best[1])):
            best = (first, last, name)
    return best[2] if best else None


def _derives(index: CodeIndex, repo_path: dict[str, str], sep: str, opts: Options) -> list[Candidate]:
    classes = [s for s in index.symbols() if s.endswith("#")]
    subs: dict[str, list[str]] = {}
    for s in classes:
        for base in index.bases(s):
            subs.setdefault(base, []).append(s)
    same: dict[str, int] = {}
    for s in classes:
        named = _name(s, sep)
        if named:
            same[named[0]] = same.get(named[0], 0) + 1
    out = []
    for base, children in sorted(subs.items()):
        named = _name(base, sep)
        defined = _target_files(index, base, repo_path) if named else None
        if named is None or defined is None or not opts.min_subclasses <= len(children) <= opts.max_gold:
            continue
        gold = []
        for child in children:
            files, child_name = _target_files(index, child, repo_path), _name(child, sep)
            if files is None or child_name is None:  # a subclass outside the tree, or a test's: not gradable
                break
            gold.append(f"{files[0]}::{child_name[0]}")
        else:
            out.append(Candidate("derives", base, named[0], named[1], defined, tuple(sorted(set(gold))),
                                 same.get(named[0], 1)))
    return out


def _choose(pool: list[Candidate], n: int, rng: random.Random, used: set[str]) -> list[Candidate]:
    """Up to ``n`` candidates with distinct names and task ids; about half of them with an ambiguous name
    when the pool has them, since telling one ``run`` from another is the part a text search gets wrong."""
    ambiguous = sorted((c for c in pool if c.ambiguous), key=lambda c: c.symbol)
    unique = sorted((c for c in pool if not c.ambiguous), key=lambda c: c.symbol)
    rng.shuffle(ambiguous)
    rng.shuffle(unique)
    out: list[Candidate] = []
    names: set[str] = set()

    def take(source: list[Candidate], limit: int) -> None:
        for c in source:
            if len(out) >= limit:
                return
            if c.name in names or task_id(c) in used:
                continue
            names.add(c.name)
            used.add(task_id(c))
            out.append(c)

    take(ambiguous, n // 2)
    take(unique, n)
    take(ambiguous, n)
    return out


_ANSWER = {
    "files": '{"files": ["<repo-relative path>", ...]}',
    "functions": '{"functions": ["<repo-relative path>::<function name>", ...]}',
    "classes": '{"classes": ["<repo-relative path>::<class name>", ...]}',
}


def _prompt(c: Candidate, key: str) -> str:
    where = f"`{c.qualified}` (defined in {', '.join(c.defined_in)})"
    if key == "files":
        ask = (f"List every file that references {where}, other than the files that define it. A reference is "
               "any use: a call, or a mention as a type or a value. Test files count.")
    elif key == "functions":
        ask = (f"List every function or method whose body references {where}, other than those in the files "
               "that define it. A reference is any use: a call, or a mention as a type or a value. Module-level "
               "code and class bodies are not a function; test functions count.")
    else:
        ask = f"List every class that derives directly from {where}: its direct subclasses, not theirs."
    amb = (f" The repository has {c.same_name} symbols named `{c.name}`; only the one above counts."
           if c.ambiguous else "")
    return (f"You are working in a checkout of this repository (the current directory). {ask}{amb} Do not modify "
            f"any files. End your answer with a fenced ```json block holding {_ANSWER[key]}.")


def _task(c: Candidate, granularity: str, opts: Options, tool: str) -> tuple[dict[str, Any], dict[str, Any]]:
    key = "classes" if c.kind == "derives" else granularity
    task = {
        "id": task_id(c), "family": "explore", "base": opts.base, "prompt": _prompt(c, key), "grader": GRADER,
        "gold": GOLD, "score": {"metric": "f1", "max": 1.0}, "max_turns": opts.max_turns,
        "question": c.kind, "answer_key": key, "symbol": c.symbol, "ambiguous_name": c.ambiguous,
        "code_index": tool,
    }
    rule = {"files": "files with a reference to the symbol, outside the files that define it",
            "functions": "functions whose range holds a reference to the symbol, outside the files that define it",
            "classes": "the classes whose listed bases include the symbol"}[key]
    gold = {"answer_key": key, "gold": list(c.gold), "exclude": list(c.defined_in) if c.kind == "callers" else [],
            "accepted": [], "symbol": c.symbol, "rule": rule}
    return task, gold
