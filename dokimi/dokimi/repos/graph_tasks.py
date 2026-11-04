# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Enumeration tasks with gold read from a SCIP code index (contract §4.7): ``eval tasks generate --from graph``.

Two questions, both graded by ``dokimi:graph_enum`` (§4.5):

- references: every file (or, on an index with enclosing ranges, every function) that references one function,
  outside the files that define it. A reference is any non-definition occurrence: SCIP does not tell a call
  from another use. C++ overloads share one qualified name, which the prompt cannot tell apart, so they are
  one target.
- derives: every class that derives directly from one class, from the index's base-class relations.

A target is a function or class defined in a tree file that is neither a test nor generated, with a gold list
of a size the options allow; dunder methods and C++ operators are never targets. No model, no agent, no
docker: the gold is what the index says, so a task is as right as the index it came from.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi.repos.code_index import CodeIndex
from dokimi.repos.code_index.symbols import descriptor_names, descriptors
from dokimi.repos.environment.files import is_generated, is_test_file
from dokimi.repos.environment.structure import detect_prefix, function_like, is_protocol_name

GOLD = "gold.json"
GRADER = "dokimi:graph_enum"
FAMILY = "navigate"
ID_MAX = 80
HASH_LEN = 8

Scopes = dict[str, list[tuple[int, int, str]]]


@dataclass(frozen=True)
class Options:
    out: Path
    base: str  # the full SHA of the revision the index was built from
    references: int = 9
    derives: int = 3
    min_gold: int = 5
    max_gold: int = 40
    min_subclasses: int = 2
    seed: int = 7
    max_turns: int = 40
    force: bool = False  # rewrite a task directory that already has a task.json


@dataclass(frozen=True)
class Candidate:
    kind: str  # references | derives
    symbols: tuple[str, ...]  # one, or a C++ function's overloads
    name: str
    qualified: str
    defined_in: tuple[str, ...]
    gold: tuple[str, ...]
    accepted: tuple[str, ...]  # right answers outside the gold: neither a hit nor a false positive
    same_name: int  # targets of this kind with this name; above 1 the prompt says which one counts

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
    references = _references(index, repo_path, granularity, sep, opts)
    derives = _derives(index, repo_path, sep, opts)
    rng = random.Random(opts.seed)
    used: set[str] = set()
    chosen = _choose(references, opts.references, rng, used) + _choose(derives, opts.derives, rng, used)
    out = Plan(granularity, prefix, len(documents), len(repo_path),
               {"references": len(references), "derives": len(derives)})
    out.outcomes = [Outcome(task_id(c), "kept" if (opts.out / task_id(c) / "task.json").exists() and not opts.force
                            else "written", c) for c in chosen]
    return out


def write(p: Plan, opts: Options, code_index: dict[str, str]) -> None:
    """Write each chosen task that is not kept: ``task.json`` and ``gold.json`` in ``<out>/<task id>/``."""
    for o in p.outcomes:
        if o.status != "written":
            continue
        task, gold = _task(o.candidate, p.granularity, opts, code_index)
        dest = opts.out / o.task
        dest.mkdir(parents=True, exist_ok=True)
        (dest / GOLD).write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (dest / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def task_id(c: Candidate) -> str:
    """``graph_<kind>_<name>_<hash>``: the hash of the symbols keeps two targets of one name (another seed,
    another revision) from sharing a directory."""
    digest = hashlib.sha256("\n".join(c.symbols).encode()).hexdigest()[:HASH_LEN]
    slug = re.sub(r"[^a-z0-9]+", "_", c.name.lower()).strip("_") or "symbol"
    return f"graph_{c.kind}_{slug}"[:ID_MAX - HASH_LEN - 1] + f"_{digest}"


def in_file_name(symbol: str) -> str | None:
    """The name inside its file: the descriptors after the leading namespaces (the module, a C++ namespace),
    joined by ``.``; ``Impl.run`` for a method, ``run`` for a module function."""
    found = descriptors(symbol)
    if not found:
        return None
    inner = [name for i, (name, _) in enumerate(found) if any(mark != "/" for _, mark in found[:i + 1])]
    return ".".join(inner) or found[-1][0]


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


def _references(index: CodeIndex, repo_path: dict[str, str], granularity: str, sep: str,
                opts: Options) -> list[Candidate]:
    groups: dict[str, list[str]] = {}  # qualified name -> its symbols (C++ overloads share one)
    short: dict[str, str] = {}
    for s in index.symbols():
        named = _name(s, sep) if function_like(s) else None
        if named:
            groups.setdefault(named[1], []).append(s)
            short[named[1]] = named[0]
    same: dict[str, int] = {}
    for q in groups:
        same[short[q]] = same.get(short[q], 0) + 1
    scopes = _function_scopes(index) if granularity == "functions" else None
    out = []
    for qualified, symbols in sorted(groups.items()):
        found = _group_referrers(index, symbols, repo_path, scopes)
        if found is not None and opts.min_gold <= len(found[1]) <= opts.max_gold:
            out.append(Candidate("references", tuple(symbols), short[qualified], qualified, found[0],
                                 tuple(sorted(found[1])), (), same[short[qualified]]))
    return out


def _group_referrers(index: CodeIndex, symbols: list[str], repo_path: dict[str, str],
                     scopes: Scopes | None) -> tuple[tuple[str, ...], set[str]] | None:
    """The defining files and the referrers of a function's symbols, or None when one is no target."""
    defined: set[str] = set()
    for s in symbols:
        files = _target_files(index, s, repo_path)
        if files is None:
            return None
        defined.update(files)
    gold: set[str] = set()
    for s in symbols:
        found = _referrers(index, s, repo_path, defined, scopes)
        if found is None:
            return None
        gold |= found
    return tuple(sorted(defined)), gold


def _referrers(index: CodeIndex, symbol: str, repo_path: dict[str, str], defined: set[str],
               scopes: Scopes | None) -> set[str] | None:
    """The files (``scopes`` None) or ``path::function`` items that reference ``symbol`` outside the files that
    define it; None when a reference lies outside the tree, since the gold would be incomplete."""
    gold: set[str] = set()
    for occ in index.references(symbol):
        path = repo_path.get(occ.path)
        if path is None:
            return None
        if path in defined:
            continue
        if scopes is None:
            gold.add(path)
            continue
        fn = _innermost(scopes.get(occ.path, ()), occ.line)
        if fn is not None:  # module-level code and class bodies are not a function
            gold.add(f"{path}::{fn}")
    return gold


def _function_scopes(index: CodeIndex) -> Scopes:
    """Per document, the line span and in-file name of each function definition that has an enclosing range."""
    scopes: Scopes = {}
    for doc in index.files():
        for occ in index.occurrences(doc):
            if occ.is_definition and occ.enclosing is not None and function_like(occ.symbol):
                name = in_file_name(occ.symbol)
                if name:
                    scopes.setdefault(doc, []).append((*occ.enclosing, name))
    return scopes


def _innermost(scopes: Any, line: int) -> str | None:
    """The function whose span holds ``line`` and starts last (a nested function is inside its parent)."""
    best: tuple[int, int, str] | None = None
    for first, last, name in scopes:
        if first <= line <= last and (best is None or (first, -last) > (best[0], -best[1])):
            best = (first, last, name)
    return best[2] if best else None


def _class_items(index: CodeIndex, symbol: str, repo_path: dict[str, str]) -> list[str] | None:
    """``path::Class`` for each tree file that defines the class, or None when it is not gradable."""
    files, name = _target_files(index, symbol, repo_path), in_file_name(symbol)
    if files is None or name is None:
        return None
    return [f"{f}::{name}" for f in files]


def _listed_items(index: CodeIndex, symbol: str, repo_path: dict[str, str]) -> list[str]:
    """``path::Class`` for every file that defines a class only accepted, a test's or one outside the tree
    included (its index path then): it is never required, so naming it must not count against the answer."""
    name = in_file_name(symbol)
    if name is None:
        return []
    return [f"{repo_path.get(occ.path, occ.path)}::{name}" for occ in index.definitions(symbol)]


def _derives(index: CodeIndex, repo_path: dict[str, str], sep: str, opts: Options) -> list[Candidate]:
    classes = [s for s in index.symbols() if s.endswith("#")]
    subs: dict[str, list[str]] = {}
    listed: dict[str, list[str]] = {}  # every class the index lists the base for, before the reduction
    for s in classes:
        for base in index.bases(s):
            subs.setdefault(base, []).append(s)
        for base in index.bases(s, minimal=False):
            listed.setdefault(base, []).append(s)
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
        items = [_class_items(index, child, repo_path) for child in children]
        if any(i is None for i in items):  # a subclass outside the tree, or a test's: not gradable
            continue
        gold = {i[0] for i in items if i}
        # a second defining file (a .pyi next to its .py), or a class whose listed bases a scip-clang
        # reduction dropped this one from: right to name, not required
        accepted = {x for i in items if i for x in i[1:]}
        for other in set(listed.get(base, ())) - set(children):
            accepted.update(_listed_items(index, other, repo_path))
        out.append(Candidate("derives", (base,), named[0], named[1], defined, tuple(sorted(gold)),
                             tuple(sorted(accepted - gold)), same.get(named[0], 1)))
    return out


def _choose(pool: list[Candidate], n: int, rng: random.Random, used: set[str]) -> list[Candidate]:
    """Up to ``n`` candidates with distinct names and task ids; about half of them with an ambiguous name
    when the pool has them, since telling one ``run`` from another is the part a text search gets wrong."""
    ambiguous = sorted((c for c in pool if c.ambiguous), key=lambda c: c.symbols)
    unique = sorted((c for c in pool if not c.ambiguous), key=lambda c: c.symbols)
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
    "functions": '{"functions": ["<repo-relative path>::<function name; Class.method for a method>", ...]}',
    "classes": '{"classes": ["<repo-relative path>::<class name>", ...]}',
}


def _prompt(c: Candidate, key: str) -> str:
    where = f"`{c.qualified}` (defined in {', '.join(c.defined_in)})"
    if len(c.symbols) > 1:
        where += f", any of its {len(c.symbols)} overloads"
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


def _task(c: Candidate, granularity: str, opts: Options,
          code_index: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    key = "classes" if c.kind == "derives" else granularity
    task = {
        "id": task_id(c), "family": FAMILY, "base": opts.base, "prompt": _prompt(c, key), "grader": GRADER,
        "gold": GOLD, "score": {"metric": "f1", "max": 1.0}, "max_turns": opts.max_turns,
        "question": c.kind, "answer_key": key, "symbols": list(c.symbols), "ambiguous_name": c.ambiguous,
        "code_index": code_index,
    }
    rule = {"files": "files with a reference to the symbol, outside the files that define it",
            "functions": "functions whose range holds a reference to the symbol, outside the files that define it",
            "classes": "the classes whose listed bases include the symbol"}[key]
    gold = {"answer_key": key, "gold": list(c.gold),
            "exclude": list(c.defined_in) if c.kind == "references" else [], "accepted": list(c.accepted),
            "symbols": list(c.symbols), "rule": rule}
    return task, gold
