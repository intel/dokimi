# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No new fixed-duration wait in ``tests/``.

A test that waits for a thread, process or timer with ``time.sleep`` or ``threading.Timer`` passes
on a fast machine and fails on a busy runner: wait on the condition with ``tests/waiting.py``
instead. This counts, per file under ``tests/``, the calls the file's imports resolve to
``time.sleep`` or ``threading.Timer`` (``import time``, ``import time as t``, ``from time import
sleep as nap``, ``from time import *``, and the same for ``threading``). Only imports are followed: a
name assigned from them (``nap = time.sleep``) is not. A ``sleep`` in a string, such as a child's shell
command, is not a call. ``tests/waiting.py`` is the one file not counted.

The counts are recorded in ``test_timing_baseline.json`` and are exact: a file may not go above its
entry, and when it goes below, the entry shrinks with it. A call that must stay ends its line with
``# timing-ok: <reason>``; a ``# timing-ok`` with no reason fails::

    python scripts/check_test_timing.py            # check; exit 1 on a problem
    python scripts/check_test_timing.py --update   # lower or drop entries; never raises (writes
                                                   # a first baseline when the file is missing)
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

PROJECT = Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"
BASELINE = PROJECT / "scripts" / "test_timing_baseline.json"

#: The helper module is where the polling loop lives, so it alone may sleep.
EXEMPT = "waiting.py"
TARGETS = {"time": {"sleep"}, "threading": {"Timer"}}
WAIVER = re.compile(r"#\s*timing-ok\b(?::(?P<reason>.*))?$")
HINT = "(python scripts/check_test_timing.py --update)"


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
Pos = tuple[int, int]  # (line, column) where a binding takes effect
Names = dict[str, list[tuple[Pos, str | None]]]  # name -> bindings in source order: "time", "time.sleep" or None


class Scope(NamedTuple):
    names: Names
    is_class: bool  # a class body is not visible from the functions inside it
    is_comp: bool  # a comprehension runs where it stands, so the scope around it is read by position too


def _end(node: ast.AST) -> Pos:
    return getattr(node, "end_lineno", 0) or 0, getattr(node, "end_col_offset", 0) or 0


def _note(names: Names, name: str, pos: Pos, meaning: str | None) -> None:
    names.setdefault(name, []).append((pos, meaning))


def _own_names(scope: ast.AST) -> Names:
    """What the names bound in ``scope`` itself mean, in source order: a module (``t -> time``), a function
    (``nap -> time.sleep``), or ``None`` for any other binding, which shadows an outer import."""
    names: Names = {}
    if isinstance(scope, FUNCTIONS):
        a = scope.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
            if arg is not None:
                _note(names, arg.arg, (0, 0), None)
    body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body  # type: ignore[attr-defined]
    for stmt in body:
        _bind(stmt, names)
    for entries in names.values():
        entries.sort(key=lambda entry: entry[0])
    return names


def _comp_names(comp: ast.AST) -> Names:
    """The targets of a comprehension are local to it for all of it."""
    names: Names = {}
    for gen in comp.generators:  # type: ignore[attr-defined]
        for name in ast.walk(gen.target):
            if isinstance(name, ast.Name):
                _note(names, name.id, (0, 0), None)
    return names


def _bind_import(node: ast.Import | ast.ImportFrom, names: Names) -> None:
    pos = _end(node)
    for alias in node.names:
        if isinstance(node, ast.Import):
            bound = alias.asname or alias.name.split(".")[0]
            full = alias.name if alias.asname else alias.name.split(".")[0]
            _note(names, bound, pos, full if full in TARGETS else None)
        elif node.module in TARGETS and node.level == 0 and alias.name == "*":
            for target in TARGETS[node.module]:
                _note(names, target, pos, f"{node.module}.{target}")
        elif node.module in TARGETS and node.level == 0 and alias.name in TARGETS[node.module]:
            _note(names, alias.asname or alias.name, pos, f"{node.module}.{alias.name}")
        else:
            _note(names, alias.asname or alias.name, pos, None)


def _bind(node: ast.AST, names: Names, at: Pos | None = None) -> None:
    """Record the names ``node`` binds in its scope, each with where it takes effect, without entering a
    nested function, class or lambda. An assignment binds once its right side has run; the targets of a
    comprehension stay in it, except a ``:=``."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _bind_import(node, names)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _note(names, node.name, _end(node), None)
    elif isinstance(node, COMPREHENSIONS):
        _bind_walruses(node, names, _end(node.generators[0].iter))
    elif not isinstance(node, ast.Lambda):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            at = _end(node)
        _bind_target(node, names, at)
        for child in ast.iter_child_nodes(node):
            _bind(child, names, at)


def _bind_walruses(node: ast.AST, names: Names, first_done: Pos) -> None:
    """A ``:=`` in a comprehension binds in the scope around it, not in a lambda inside it. The first
    iterable runs before the element, so the binding takes effect after it, wherever it stands in the text."""
    if isinstance(node, ast.Lambda):
        return
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        _note(names, node.target.id, max(_end(node), first_done), None)
    for child in ast.iter_child_nodes(node):
        _bind_walruses(child, names, first_done)


def _bind_target(node: ast.AST, names: Names, at: Pos | None) -> None:
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        _note(names, node.id, at or _end(node), None)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        _note(names, node.name, (node.lineno, node.col_offset), None)


def _resolve(call: ast.Call, scopes: list[Scope]) -> str | None:
    """``"time.sleep"`` or ``"threading.Timer"`` when ``call`` is one, by the innermost binding of its name.
    In the scope the call stands in, that is the last binding before the call; in a scope around a
    function, the last binding of the scope."""
    pos = (call.lineno, call.col_offset)

    def meaning(name: str) -> str | None:
        by_position = True
        for scope in scopes:
            entries = scope.names.get(name)
            if entries:
                seen = [m for p, m in entries if not by_position or p <= pos]
                if seen:
                    return seen[-1]
                if not scope.is_class:
                    return None
            by_position = by_position and scope.is_comp
        return None

    func = call.func
    if isinstance(func, ast.Name):
        target = meaning(func.id)
        return target if target and "." in target else None
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = meaning(func.value.id)
        if module in TARGETS and func.attr in TARGETS[module]:
            return f"{module}.{func.attr}"
    return None


def _outer_parts(node: ast.AST) -> list[ast.AST]:
    """What a function, lambda or class evaluates in the scope around it: decorators, bases, defaults."""
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    a = node.args  # type: ignore[attr-defined]
    outer: list[ast.AST] = [*a.defaults, *(d for d in a.kw_defaults if d is not None)]
    if not isinstance(node, ast.Lambda):
        outer += node.decorator_list  # type: ignore[attr-defined]
    return outer


class _Walker:
    """Finds the calls that resolve to a sleep or timer. A name means what the innermost scope that binds
    it says; a class body is not visible from the functions inside it."""

    def __init__(self) -> None:
        self.found: list[tuple[ast.Call, str]] = []
        self.done: set[int] = set()  # the first iterable of a comprehension, walked in the outer scope

    def comprehension(self, node: ast.AST, scopes: list[Scope]) -> None:
        first = node.generators[0].iter  # type: ignore[attr-defined]
        self.walk(first, scopes)
        self.done.add(id(first))
        inner = [Scope(_comp_names(node), False, True), *(s for s in scopes if not s.is_class)]
        for child in ast.iter_child_nodes(node):
            self.walk(child, inner)

    def definition(self, node: ast.AST, scopes: list[Scope]) -> None:
        for child in _outer_parts(node):
            self.walk(child, scopes)
        inner = [Scope(_own_names(node), isinstance(node, ast.ClassDef), False)]
        inner += [s for s in scopes if not s.is_class]
        body = [node.body] if isinstance(node, ast.Lambda) else node.body  # type: ignore[attr-defined]
        for child in body:
            self.walk(child, inner)

    def walk(self, node: ast.AST, scopes: list[Scope]) -> None:
        if id(node) in self.done:
            return
        if isinstance(node, COMPREHENSIONS):
            self.comprehension(node, scopes)
            return
        if isinstance(node, ast.Call):
            target = _resolve(node, scopes)
            if target:
                self.found.append((node, target))
        if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            self.definition(node, scopes)
            return
        for child in ast.iter_child_nodes(node):
            self.walk(child, scopes)


def _calls(tree: ast.Module) -> list[tuple[ast.Call, str]]:
    """Every call that resolves to a sleep or timer, with its target."""
    walker = _Walker()
    walker.walk(tree, [Scope(_own_names(tree), False, False)])
    return walker.found


def _waivers(text: str) -> tuple[set[int], list[int]]:
    """Lines with a ``# timing-ok: <reason>`` comment, and lines with one that has no reason."""
    ok: set[int] = set()
    bare: list[int] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        match = WAIVER.match(tok.string) if tok.type == tokenize.COMMENT else None
        if match is None:
            continue
        if re.search(r"\w", match.group("reason") or ""):
            ok.add(tok.start[0])
        else:
            bare.append(tok.start[0])
    return ok, bare


def scan(text: str, filename: str) -> tuple[dict[str, int], list[int]]:
    """``({"time.sleep": n, "threading.Timer": m}, bare waiver lines)`` of one file's source."""
    tree = ast.parse(text, filename=filename)
    ok, bare = _waivers(text)
    counts: dict[str, int] = {}
    for node, target in _calls(tree):
        lines = {node.lineno, node.end_lineno or node.lineno}
        if not lines & ok:
            counts[target] = counts.get(target, 0) + 1
    return counts, bare


def measure(root: Path) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """``(calls per file, calls per target, problems)`` over every ``*.py`` under ``root``."""
    per_file: dict[str, int] = {}
    per_target: dict[str, int] = {}
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if path.relative_to(root).as_posix() == EXEMPT or any(
                p == "__pycache__" or p.startswith(".") for p in parts):
            continue
        key = f"{root.name}/{path.relative_to(root).as_posix()}"
        try:
            counts, bare = scan(path.read_text(encoding="utf-8"), key)
        except (OSError, SyntaxError, ValueError, tokenize.TokenError) as exc:
            problems.append(f"{key}: cannot be scanned ({type(exc).__name__}): {exc}")
            continue
        problems += [f"{key}:{line}: `# timing-ok` needs a reason: `# timing-ok: <why this sleep must stay>`"
                     for line in bare]
        if counts:
            per_file[key] = sum(counts.values())
        for target, n in counts.items():
            per_target[target] = per_target.get(target, 0) + n
    return per_file, per_target, problems


def load_baseline(path: Path) -> dict[str, int]:
    """The ``files`` counts of the baseline; ``ValueError`` when the file is not one."""
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files", {}) if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise ValueError('expected {"files": {"<file>": <count>}}')
    try:
        return {k: int(v) for k, v in files.items()}
    except (TypeError, ValueError):
        raise ValueError("a count is not an integer") from None


def dump_baseline(files: dict[str, int]) -> str:
    return json.dumps({"files": dict(sorted(files.items()))}, indent=2) + "\n"


def violations(now: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """``(above, below)``: files over their entry (or with none), and files under it."""
    above: list[str] = []
    below: list[str] = []
    for key in sorted(set(now) | set(baseline)):
        have, entry = now.get(key, 0), baseline.get(key)
        if have > (entry or 0):
            was = "no baseline entry" if entry is None else f"its baseline {entry}"
            above.append(f"{key}: {have} sleep/Timer call(s), above {was}; wait on the condition with "
                         "tests/waiting.py, or end the line with `# timing-ok: <reason>`")
        elif entry is not None and have < entry:
            below.append(f"{key}: {have} sleep/Timer call(s), below its baseline {entry}; lower the "
                         f"entry so the gain stays {HINT}")
    return above, below


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--update", action="store_true",
                    help="lower or drop baseline entries to match the code; never raises one "
                         "(a missing baseline file is written with the current count of every file)")
    ap.add_argument("--tests-root", type=Path, default=TESTS, help="directory to scan (default: tests/)")
    ap.add_argument("--baseline", type=Path, default=BASELINE, help="baseline file to read and write")
    args = ap.parse_args(argv)

    if not args.tests_root.is_dir():
        print(f"--tests-root {args.tests_root} is not a directory")
        return 2
    per_file, per_target, problems = measure(args.tests_root)
    summary = (f"{sum(per_file.values())} sleep/Timer call(s) in {len(per_file)} file(s): "
               + ", ".join(f"{n} {t}" for t, n in sorted(per_target.items())))
    if args.update and not args.baseline.exists() and not problems:
        args.baseline.write_text(dump_baseline(per_file), encoding="utf-8")
        print(f"wrote {args.baseline.name}; {summary}")
        return 0
    if not args.baseline.exists():
        print(f"no {args.baseline.name}; create it with --update {HINT}")
        return 1
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as exc:
        print(f"{args.baseline.name} is not a readable baseline: {exc}")
        return 1
    above, below = violations(per_file, baseline)
    if args.update:
        found = problems + above
        if found:
            print(f"refusing to update {args.baseline.name}: it only shrinks")
            print("\n".join(found))
            return 1
        args.baseline.write_text(dump_baseline({k: min(v, per_file.get(k, 0)) for k, v in baseline.items()
                                                if per_file.get(k, 0) > 0}), encoding="utf-8")
        print(f"wrote {args.baseline.name}; {summary}")
        return 0
    found = problems + above + below
    if found:
        print("\n".join(found))
        print(f"\n{len(found)} test timing problem(s); see the docstring of scripts/check_test_timing.py")
        return 1
    print(f"test timing ratchet ok: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
