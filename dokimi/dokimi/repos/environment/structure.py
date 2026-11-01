# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The structure section of ``repo inspect --index``: modules and how the code ties them together.

Read from one SCIP index someone else built (:mod:`dokimi.repos.code_index`); no indexer is run. A module
is a directory: one with more indexed non-test files than :func:`module_limit` is split into its
subdirectories, the files right in it staying its own. Per module:

* ``files``: the indexed non-test files in it;
* ``reference_fanin``: the non-test files outside it that reference a function or method defined in it.
  SCIP records references, not call expressions, so this counts a type use or a header declaration too;
  it is not a caller count;
* ``other_tree_modules``: the modules of another tree it shares a reference with, either way. A tree is
  the first two path components (``cpp/daal`` and ``cpp/oneapi`` in oneDAL); a module shallower than
  that is of the same tree as the modules under it;
* ``ambiguity``: the share of its function and method names that another module defines too;
* ``in_guidance``: whether an instruction file names its path, or its name between ``/`` or backticks.

These are hypotheses about where an agent may lose its way, not measured effects: the findings drawn
from them are ``info`` with ``low`` confidence.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from dokimi.repos.code_index import CodeIndex
from dokimi.repos.code_index.symbols import descriptor_name, is_local

from .files import is_test_file

MIN_FILES = 3
MIN_FANIN = 3
TREE_DEPTH = 2
AMBIGUOUS = 0.5
HOTSPOTS = 10  # the kept modules with most reference fan-in that findings are drawn for
BRIDGES = 5  # the other-tree modules a hotspot shares references with before it gets a finding


def module_limit(total: int) -> int:
    """The most indexed files a module holds before it is split into its subdirectories."""
    return max(30, total // 20)


def rules(limit: int) -> dict[str, int | float]:
    return {"module_limit": limit, "tree_depth": TREE_DEPTH, "min_files": MIN_FILES, "min_fanin": MIN_FANIN,
            "hotspots": HOTSPOTS, "min_ambiguity": AMBIGUOUS, "min_other_tree_modules": BRIDGES}


def detect_prefix(documents: Iterable[str], tree_files: Iterable[str]) -> tuple[str, int]:
    """The prefix that maps the index's document paths onto the tree, and how many documents it maps.

    An index written relative to a subdirectory (dokimi's own is relative to ``dokimi/``) lists ``a/b.py``
    for the tree's ``dokimi/a/b.py``. Each document votes for every prefix that makes it a tree file; the
    prefix with most votes wins, the shortest on a tie."""
    files = set(tree_files)
    by_base: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_base[posixpath.basename(f)].append(f)
    votes: Counter[str] = Counter()
    for doc in documents:
        for f in by_base.get(posixpath.basename(doc), ()):
            if f == doc:
                votes[""] += 1
            elif f.endswith("/" + doc):
                votes[f[:-len(doc)]] += 1
    if not votes:
        return "", 0
    prefix = min(votes, key=lambda p: (-votes[p], len(p), p))
    return prefix, votes[prefix]


def _assign(files: list[str], limit: int) -> dict[str, str]:
    """File -> module (its directory, or the ancestor directory that was not split)."""
    out: dict[str, str] = {}

    def walk(directory: str, members: list[str]) -> None:
        depth = len(directory.split("/")) if directory else 0
        nested = [f for f in members if posixpath.dirname(f) != directory]
        if len(members) <= limit or not nested:
            for f in members:
                out[f] = directory or "."
            return
        groups: dict[str, list[str]] = defaultdict(list)
        for f in members:
            if posixpath.dirname(f) == directory:
                out[f] = directory or "."
            else:
                groups["/".join(f.split("/")[:depth + 1])].append(f)
        for child, sub in sorted(groups.items()):
            walk(child, sub)

    walk("", files)
    return out


def tree_of(module: str) -> str:
    """The tree of a module: its first two path components (``cpp/daal``, ``cpp/oneapi``, ``dokimi/dokimi``)."""
    return "/".join(module.split("/")[:TREE_DEPTH])


def same_tree(a: str, b: str) -> bool:
    """Whether two modules are of one tree: ``pkg`` and ``pkg/sub`` are, ``.`` is of none but its own."""
    ta, tb = tree_of(a), tree_of(b)
    return ta == tb or tb.startswith(ta + "/") or ta.startswith(tb + "/")


def mentioned(module: str, text: str) -> bool:
    """Whether ``text`` names the module: its path as a token, or its name between ``/`` or backticks."""
    name = module.rsplit("/", 1)[-1]
    if module == "." or name not in text:  # the substring test spares the regexes on most modules
        return False
    return (re.search(rf"[`/]{re.escape(name)}[`/]", text) is not None
            or re.search(rf"(?<![\w/.]){re.escape(module)}(?![\w/])", text) is not None)


def _function_like(symbol: str) -> bool:
    # scip-clang and scip-python both end a function or method symbol with `).`
    return symbol.endswith(").") and not is_local(symbol)


def analyze(index: CodeIndex, tree_files: Iterable[str], guidance: str, limit: int | None = None) -> dict[str, Any]:
    """The ``structure`` section for an index read with no root prefix, against the tree's file list.

    ``limit`` overrides :func:`module_limit` (the most files a module holds before it is split)."""
    tree_files = list(tree_files)
    documents = index.files()
    prefix, _ = detect_prefix(documents, tree_files)
    in_tree = set(tree_files)
    repo_path = {doc: prefix + doc for doc in documents if prefix + doc in in_tree}
    code = sorted(p for p in repo_path.values() if not is_test_file(p))
    limit = module_limit(len(code)) if limit is None else limit
    module_of = _assign(code, limit)

    defined_in, by_name, names_of = _definitions(index, repo_path, module_of)
    fanin, edges = _references(index, repo_path, module_of, defined_in)
    members: dict[str, int] = Counter(module_of.values())
    modules = [_row(mod, members[mod], names_of[mod], by_name, fanin[mod], edges[mod], guidance)
               for mod in sorted(members)]
    modules.sort(key=lambda m: (-m["reference_fanin"], m["module"]))
    return {
        "index": {"format": "scip", "tool": index.tool, "documents": len(documents), "root_prefix": prefix,
                  "documents_in_tree": len(repo_path), "documents_not_in_tree": len(documents) - len(repo_path)},
        "rules": rules(limit),
        "modules": modules,
    }


Modules = dict[str, set[str]]


def _definitions(index: CodeIndex, repo_path: dict[str, str],
                 module_of: dict[str, str]) -> tuple[Modules, Modules, Modules]:
    """Function-like symbol -> modules defining it; name -> modules defining it; module -> its names."""
    defined_in: Modules = defaultdict(set)
    for doc, path in repo_path.items():
        mod = module_of.get(path)
        if mod is None:
            continue
        for occ in index.occurrences(doc):
            if occ.is_definition and _function_like(occ.symbol):
                defined_in[occ.symbol].add(mod)
    by_name: Modules = defaultdict(set)
    for symbol, mods in defined_in.items():
        by_name[descriptor_name(symbol) or ""] |= mods
    by_name.pop("", None)
    names_of: Modules = defaultdict(set)
    for name, mods in by_name.items():
        for mod in mods:
            names_of[mod].add(name)
    return defined_in, by_name, names_of


def _references(index: CodeIndex, repo_path: dict[str, str], module_of: dict[str, str],
                defined_in: Modules) -> tuple[Modules, Modules]:
    """Module -> the files outside it that reference it; module -> the modules it shares a reference with."""
    fanin: Modules = defaultdict(set)
    edges: Modules = defaultdict(set)
    for doc, path in repo_path.items():
        caller = module_of.get(path)
        if caller is None:  # a test file: its references say nothing about the code's own structure
            continue
        for occ in index.occurrences(doc):
            targets = () if occ.is_definition else defined_in.get(occ.symbol, ())
            for mod in targets:
                if mod != caller:
                    fanin[mod].add(path)
                    edges[mod].add(caller)
                    edges[caller].add(mod)
    return fanin, edges


def _row(mod: str, files: int, names: set[str], by_name: Modules, fanin: set[str], edges: set[str],
         guidance: str) -> dict[str, Any]:
    ambiguity = sum(len(by_name[n]) > 1 for n in names) / len(names) if names else 0.0
    return {
        "module": mod, "files": files, "reference_fanin": len(fanin),
        "other_tree_modules": sum(not same_tree(other, mod) for other in edges),
        "ambiguity": round(ambiguity, 3), "names": len(names), "in_guidance": mentioned(mod, guidance),
        "kept": files >= MIN_FILES and len(fanin) >= MIN_FANIN,
    }
