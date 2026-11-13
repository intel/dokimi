#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No-new-clones ratchet: near-duplicate code blocks in first-party Python.

Over ``dokimi/``, ``scripts/`` and ``cli.py`` (tests repeat setup on purpose
and are left out), two kinds of block are fingerprinted:

- **Function bodies** of at least ``MIN_NODES`` AST nodes (docstring dropped).
- **Statement windows**: runs of two or more consecutive statements in one
  block (a module, class, function, loop or branch body), the shortest such run
  from each starting statement that reaches ``MIN_NODES`` nodes.

A fingerprint hashes the block's AST after renaming every name bound in it (an
assignment target, a loop variable, an argument) by order of first use, so a
copy with renamed locals still matches. Free names (builtins, imports, globals),
attributes and literal values are kept: blanking literals too matched argparse
builders and runs of ``re.compile`` constants that share no code. A fingerprint
seen at two or more places is a clone group; of overlapping matches (a sliding
copy, a run repeated within itself) only the first is kept.

Clone groups that existed when the gate was introduced are recorded in
``duplicates_baseline.json`` with their exact count. A count may not grow, and
when it shrinks the entry must shrink with it (``--update``). A new group fails.
An entry added or raised against the base branch's baseline needs a "rationale".
Fix a failure by extracting the shared code; a baseline entry is for a clone
that cannot be shared, and then the PR says why.

    python scripts/check_duplicates.py            # check; exit 1 on a violation
    python scripts/check_duplicates.py --update   # lower or drop entries; never raises
    python scripts/check_duplicates.py --seed     # record every current group
    python scripts/check_duplicates.py --report   # list every group; exit 0
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BASELINE = PROJECT / "scripts" / "duplicates_baseline.json"
sys.path.insert(0, str(PROJECT / "scripts"))
from check_growth import RATIONALE_MAX, SEED_RATIONALE, base_ref  # noqa: E402

#: Smallest block, in AST nodes, that is fingerprinted: two dense lines or about
#: five plain ones. At 40 every group on the tree was a verbatim copy; at 30, 28 groups.
MIN_NODES = 40
ROOTS = ("dokimi", "scripts")
TOP_FILES = ("cli.py",)
_BODIES = ("body", "orelse", "finalbody")
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SKIP_FIELDS = {"ctx", "type_comment"}


@dataclass(frozen=True)
class Block:
    rel: str
    lo: int
    hi: int
    kind: str    # "function" or "window"

    @property
    def where(self) -> str:
        return f"{self.rel}:{self.lo}-{self.hi}"

    def overlaps(self, other: Block) -> bool:
        return self.rel == other.rel and self.lo <= other.hi and other.lo <= self.hi


def python_files(project: Path) -> list[Path]:
    found = [project / name for name in TOP_FILES if (project / name).is_file()]
    for root in ROOTS:
        for path in (project / root).rglob("*.py"):
            parts = path.relative_to(project).parts
            if not any(p == "__pycache__" or p.startswith(".") for p in parts):
                found.append(path)
    return sorted(found)


@dataclass
class _Stmt:
    """One statement serialised once; a name token is ("N", id)."""
    tokens: list
    bound: set[str]
    nodes: int
    shape: int = 0   # hash of the tokens with every name blanked: a cheap pre-filter


def _leaf(node: ast.AST, out: _Stmt) -> bool:
    """Emit a literal or a name; True when the node has nothing more to emit."""
    if isinstance(node, ast.Constant):
        out.tokens.append(f"C:{node.value!r}")
        return True
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            out.bound.add(node.id)
        out.tokens.append(("N", node.id))
        return True
    if isinstance(node, ast.arg):
        out.bound.add(node.arg)
        out.tokens.append(("N", node.arg))
    return False


def _serialise(node: ast.AST, out: _Stmt, cache: dict[int, _Stmt]) -> None:
    if isinstance(node, ast.stmt) and id(node) in cache:
        done = cache[id(node)]
        out.tokens += done.tokens
        out.bound |= done.bound
        out.nodes += done.nodes
        return
    out.nodes += 1
    if _leaf(node, out):
        return
    out.tokens.append(type(node).__name__)
    for field, value in ast.iter_fields(node):
        if field in _SKIP_FIELDS or (isinstance(node, ast.arg) and field == "arg"):
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ast.AST):
                _serialise(item, out, cache)
            elif item:   # falsy scalars skipped: new fields (3.15's is_lazy) default to them
                out.tokens.append(f"{field}={item}")
    out.tokens.append(")")


def _stmt(node: ast.stmt, cache: dict[int, _Stmt]) -> _Stmt:
    if id(node) not in cache:
        s = _Stmt([], set(), 0)
        _serialise(node, s, cache)
        s.shape = hash(tuple("N" if isinstance(t, tuple) else t for t in s.tokens))
        cache[id(node)] = s
    return cache[id(node)]


def fingerprint(stmts: list[_Stmt]) -> str:
    """Names bound in the block are renamed by first use; free names (builtins,
    imports, globals) are kept, so calls to different functions differ."""
    bound = set().union(*(s.bound for s in stmts))
    alias: dict[str, str] = {}
    parts = []
    for s in stmts:
        for t in s.tokens:
            if isinstance(t, tuple):
                t = alias.setdefault(t[1], f"v{len(alias)}") if t[1] in bound else t[1]
            parts.append(t)
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:12]


def _strip_docstring(stmts: list[ast.stmt]) -> list[ast.stmt]:
    first = stmts[0] if stmts else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        return stmts[1:]
    return stmts


def _windows(stmts: list[ast.stmt], cache: dict[int, _Stmt]) -> list[tuple[int, int]]:
    """(i, j) of the shortest run stmts[i:j] from each i that reaches MIN_NODES."""
    sizes = [_stmt(s, cache).nodes for s in stmts]
    out, j, total = [], 0, 0
    for i in range(len(stmts)):
        while j < len(stmts) and total < MIN_NODES:
            total += sizes[j]
            j += 1
        if total >= MIN_NODES and j - i > 1:
            out.append((i, j))
        total -= sizes[i]
    return out


def _bodies(node: ast.AST) -> list[list[ast.stmt]]:
    found = [getattr(node, f, None) for f in _BODIES]
    found += [c.body for c in getattr(node, "cases", [])]
    return [b for b in found if isinstance(b, list) and b and isinstance(b[0], ast.stmt)]


def _hi(stmts: list[ast.stmt]) -> int:
    return max(s.end_lineno or s.lineno for s in stmts)


def _candidate(parts: list[_Stmt], block: Block) -> tuple[int, list[_Stmt], Block]:
    return hash(tuple(p.shape for p in parts)), parts, block


def blocks(tree: ast.Module, rel: str) -> list[tuple[int, list[_Stmt], Block]]:
    """Every (shape, statements, block) of one module; fingerprint() hashes the statements."""
    out: list[tuple[int, list[_Stmt], Block]] = []
    cache: dict[int, _Stmt] = {}
    nodes = list(ast.walk(tree))
    for node in reversed(nodes):   # breadth-first reversed: children are cached first
        if isinstance(node, ast.stmt):
            _stmt(node, cache)
    for node in nodes:
        if isinstance(node, _FUNCS):
            body = _strip_docstring(node.body)
            parts = [_stmt(s, cache) for s in body]
            if sum(p.nodes for p in parts) >= MIN_NODES:
                out.append(_candidate(parts, Block(rel, node.lineno, _hi(body), "function")))
        if not isinstance(node, (ast.stmt, ast.Module, ast.ExceptHandler, ast.match_case)):
            continue
        for body in _bodies(node):
            body = _strip_docstring(body)
            for i, j in _windows(body, cache):
                parts = [_stmt(s, cache) for s in body[i:j]]
                block = Block(rel, body[i].lineno, _hi(body[i:j]), "window")
                out.append(_candidate(parts, block))
    return out


def _distinct(bs: list[Block]) -> list[Block]:
    """Members that do not overlap an earlier member (a repeated line run matches itself)."""
    kept: list[Block] = []
    for b in sorted(bs, key=lambda b: (b.kind != "function", b.rel, b.lo)):
        if not any(b.overlaps(k) for k in kept):
            kept.append(b)
    return kept


def clone_groups(project: Path | None = None) -> dict[str, list[str]]:
    """Fingerprint -> sorted locations, for every fingerprint seen twice or more.

    A sliding copy matches many overlapping windows; a window group whose every
    member overlaps a member of a group already kept adds nothing and is dropped."""
    project = project or PROJECT
    by_shape: dict[int, list[tuple[list[_Stmt], Block]]] = defaultdict(list)
    for path in python_files(project):
        rel = path.relative_to(project).as_posix()
        for shape, parts, block in blocks(ast.parse(path.read_text(encoding="utf-8"), filename=rel), rel):
            by_shape[shape].append((parts, block))
    seen: dict[str, list[Block]] = defaultdict(list)
    for found in by_shape.values():
        for parts, block in found if len(found) > 1 else ():
            seen[fingerprint(parts)].append(block)
    groups = {fp: _distinct(bs) for fp, bs in seen.items()}
    order = sorted((fp for fp, bs in groups.items() if len(bs) > 1),
                   key=lambda fp: (groups[fp][0].kind != "function", groups[fp][0].rel,
                                   groups[fp][0].lo))
    taken: list[Block] = []
    kept = {}
    for fp in order:
        if all(any(b.overlaps(t) for t in taken) for b in groups[fp]):
            continue
        taken += groups[fp]
        kept[fp] = sorted(b.where for b in groups[fp])
    return kept


def load_baseline(path: Path | None = None) -> dict[str, dict]:
    return json.loads((path or BASELINE).read_text(encoding="utf-8"))["groups"]


def dump_baseline(groups: dict[str, dict]) -> str:
    lines = [f"    {json.dumps(k)}: {json.dumps(v, sort_keys=True)}" for k, v in sorted(groups.items())]
    return '{\n  "groups": {\n' + ",\n".join(lines) + ("\n" if lines else "") + "  }\n}\n"


def violations(groups: dict[str, list[str]], baseline: dict[str, dict]) -> list[str]:
    found = []
    for fp, where in sorted(groups.items()):
        allowed = baseline.get(fp, {}).get("count", 0)
        if len(where) > allowed:
            label = "new clone group" if not allowed else f"clone group grew past {allowed}"
            found.append(f"{label} ({len(where)} copies, {fp}): " + ", ".join(where))
    for fp, entry in sorted(baseline.items()):
        now = len(groups.get(fp, []))
        if now < entry["count"]:
            found.append(f"stale entry {fp}: {now} copies now, baseline says {entry['count']} "
                         "(run `python scripts/check_duplicates.py --update`)")
    return found


def load_base_baseline(ref: str) -> dict[str, dict] | None:
    """The baseline at the commit HEAD forked from ``ref``; None when git, the ref or
    the file there is missing (then raised entries go unchecked)."""
    try:
        fork = subprocess.run(["git", "merge-base", "HEAD", ref], cwd=PROJECT,
                              capture_output=True, text=True, check=False)
        point = fork.stdout.strip() if fork.returncode == 0 else ref
        shown = subprocess.run(["git", "show", f"{point}:./scripts/{BASELINE.name}"], cwd=PROJECT,
                               capture_output=True, text=True, check=False)
    except OSError:
        return None
    return json.loads(shown.stdout)["groups"] if shown.returncode == 0 else None


def raised_without_rationale(baseline: dict[str, dict], base: dict[str, dict]) -> list[str]:
    """Entries added or raised relative to the base branch's baseline with no new
    "rationale": a tolerated clone says why it cannot be shared."""
    found = []
    for fp, entry in sorted(baseline.items()):
        old = base.get(fp, {})
        if old and entry["count"] <= old["count"]:
            continue
        why = str(entry.get("rationale", "")).strip()
        if why in ("", SEED_RATIONALE, str(old.get("rationale", "")).strip()):
            change = "added" if not old else f"raised from {old['count']} to {entry['count']}"
            found.append(f"{fp}: {change} without a \"rationale\" (at most {RATIONALE_MAX} "
                         "characters: why the copies cannot share one function)")
    return found


def _files(where: list[str]) -> list[str]:
    """The files of a group, without line numbers: an edit elsewhere does not churn the baseline."""
    return sorted({w.rsplit(":", 1)[0] for w in where})


def tightened(groups: dict[str, list[str]], baseline: dict[str, dict]) -> dict[str, dict]:
    out = {}
    for fp, entry in baseline.items():
        n = min(entry["count"], len(groups.get(fp, [])))
        if n > 1:
            out[fp] = {**entry, "count": n, "where": _files(groups[fp])}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="lower or drop entries; never raises")
    mode.add_argument("--seed", action="store_true", help="record every current clone group")
    mode.add_argument("--report", action="store_true", help="list every clone group; exit 0")
    args = ap.parse_args(argv)
    groups = clone_groups()
    if args.report:
        for fp, where in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"{fp} x{len(where)}: " + ", ".join(where))
        print(f"{len(groups)} clone group(s)")
        return 0
    if args.seed or args.update:
        new = ({fp: {"count": len(w), "rationale": SEED_RATIONALE, "where": _files(w)} for fp, w in groups.items()} if args.seed
               else tightened(groups, load_baseline()))
        BASELINE.write_text(dump_baseline(new), encoding="utf-8")
        print(f"wrote {BASELINE.relative_to(PROJECT).as_posix()} ({len(new)} group(s))")
        return 0
    baseline = load_baseline()
    found = violations(groups, baseline)
    base = load_base_baseline(base_ref())
    if base is None:
        print(f"note: no {BASELINE.name} at {base_ref()}; added entries not checked for a rationale")
    else:
        found += raised_without_rationale(baseline, base)
    for v in found:
        print(v)
    if found:
        print(f"\n{len(found)} duplicate-code violation(s); see the docstring of "
              "scripts/check_duplicates.py")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
