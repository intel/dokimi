# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No new fixed-duration wait in ``tests/``.

A test that waits for a thread, process or timer with ``time.sleep`` or ``threading.Timer`` passes
on a fast machine and fails on a busy runner: wait on the condition with ``tests/waiting.py``
instead. This counts, per file under ``tests/``, the calls the file's imports resolve to
``time.sleep`` or ``threading.Timer`` (``import time``, ``import time as t``, ``from time import
sleep as nap``, ``from time import *``, and the same for ``threading``). Only imports are followed: a
name assigned from them (``nap = time.sleep``) is not. Where branches meet (``if``/``else``, ``try``/
``except``, ``case``), a name counts when any branch binds it to a sleep or timer. A ``sleep`` in a string, such as a child's shell
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
from collections.abc import Sequence
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
Span = tuple[Pos, Pos]  # the text where a binding is not yet in effect, though it stands before it


class Arm(NamedTuple):
    """One arm of a branching statement, with the text of every arm of it: a binding in an arm may or may
    not have run, and one in a different arm of the same statement never ran with it."""

    key: object
    side: int
    spans: tuple[Span, ...]


Arms = tuple[Arm, ...]  # the arms a binding stands in, outermost first
#: name -> bindings in source order: (position, "time" / "time.sleep" / None, spans that do not see it, arms)
Names = dict[str, list[tuple[Pos, str | None, tuple[Span, ...], Arms]]]


class Scope(NamedTuple):
    names: Names
    is_class: bool  # a class body is not visible from the functions inside it
    is_comp: bool  # a comprehension runs where it stands, so the scope around it is read by position too


def _end(node: ast.AST) -> Pos:
    return getattr(node, "end_lineno", 0) or 0, getattr(node, "end_col_offset", 0) or 0


def _span(node: ast.AST) -> Span:
    return (node.lineno, node.col_offset), _end(node)  # type: ignore[attr-defined]


def _note(names: Names, name: str, pos: Pos, meaning: str | None, unseen: tuple[Span, ...] = (),
          path: Arms = ()) -> None:
    names.setdefault(name, []).append((pos, meaning, unseen, path))


def _group_span(group: Sequence[ast.AST]) -> Span:
    """The text of statements, handlers or cases that stand together; a ``case`` has no position of its own."""
    if not group:
        return (0, 0), (0, 0)
    first, last = group[0], group[-1]
    if isinstance(first, ast.match_case):
        first = first.pattern
    if isinstance(last, ast.match_case):
        last = last.body[-1]
    return (first.lineno, first.col_offset), _end(last)  # type: ignore[attr-defined]


def _child_arms(node: ast.AST) -> dict[int, Arm]:
    """The arm each child of a branching statement stands in: the two sides of an ``if``, the ``try`` body
    (with its ``else``) and each handler, each ``case``. A loop body and its ``else`` may or may not run,
    but not instead of one another, so each is an arm alone."""
    groups: list[Sequence[ast.AST]]
    if isinstance(node, ast.If):
        groups = [node.body, node.orelse]
    elif isinstance(node, (ast.Try, ast.TryStar)):
        groups = [[*node.body, *node.orelse], *([handler] for handler in node.handlers)]
    elif isinstance(node, ast.Match):
        groups = [[case] for case in node.cases]
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        return {id(child): Arm((id(node), n), 0, (_group_span(group),))
                for n, group in enumerate((node.body, node.orelse)) for child in group}
    else:
        return {}
    spans = tuple(_group_span(group) for group in groups)
    return {id(child): Arm(id(node), n, spans) for n, group in enumerate(groups) for child in group}


def _survivors(entries: list[tuple[Pos, str | None, tuple[Span, ...], Arms]], pos: Pos) -> list[str | None]:
    """What a name may mean at ``pos`` after these bindings (in source order): the last one, and each earlier
    one that no later binding made on every path to it, such as the other arm of an ``if``. A binding in an
    arm the call is not in never ran with it. A binding in an arm the call stands in ran before it."""
    out: list[str | None] = []
    later: list[Arms] = []
    for _, meaning, _, path in reversed(entries):
        if any(a.side != n and lo <= pos < hi for a in path for n, (lo, hi) in enumerate(a.spans)):
            continue
        if not any(path[:len(other)] == other for other in later):
            out.append(meaning)
        later.append(path)
        if path and all(lo <= pos < hi for a in path for n, (lo, hi) in enumerate(a.spans) if n == a.side):
            break
    return out


def _declared(nodes: list[ast.AST]) -> set[str]:
    """The names a ``global`` or ``nonlocal`` statement in these statements hands to another scope."""
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
        elif not isinstance(node, (*FUNCTIONS, ast.ClassDef)):
            out |= _declared(list(ast.iter_child_nodes(node)))
    return out


def _own_names(scope: ast.AST, postponed: bool = False) -> Names:
    """What the names bound in ``scope`` itself mean, in source order: a module (``t -> time``), a function
    (``nap -> time.sleep``), or ``None`` for any other binding, which shadows an outer import."""
    names: Names = {}
    if isinstance(scope, FUNCTIONS):
        a = scope.args
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
            if arg is not None:
                _note(names, arg.arg, (0, 0), None)
    body = [scope.body] if isinstance(scope, ast.Lambda) else scope.body  # type: ignore[attr-defined]
    local = isinstance(scope, FUNCTIONS)
    for stmt in body:
        _bind(stmt, names, local=local, postponed=postponed)
    for name in set() if isinstance(scope, ast.Module) else _declared(body):
        kept = [entry for entry in names.get(name, []) if entry[1] is not None]  # an import still counts
        if kept:
            names[name] = kept
        else:
            names.pop(name, None)
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


def _bind_import(node: ast.Import | ast.ImportFrom, names: Names, path: Arms) -> None:
    pos = _end(node)
    for alias in node.names:
        if isinstance(node, ast.Import):
            bound = alias.asname or alias.name.split(".")[0]
            full = alias.name if alias.asname else alias.name.split(".")[0]
            _note(names, bound, pos, full if full in TARGETS else None, path=path)
        elif node.module in TARGETS and node.level == 0 and alias.name == "*":
            for target in TARGETS[node.module]:
                _note(names, target, pos, f"{node.module}.{target}", path=path)
        elif node.module in TARGETS and node.level == 0 and alias.name in TARGETS[node.module]:
            _note(names, alias.asname or alias.name, pos, f"{node.module}.{alias.name}", path=path)
        else:
            _note(names, alias.asname or alias.name, pos, None, path=path)


def _bind(node: ast.AST, names: Names, at: Pos | None = None, *, local: bool = False,
          postponed: bool = False, path: Arms = ()) -> None:
    """Record the names ``node`` binds in its scope, each with where it takes effect, without entering a
    nested function, class or lambda. An assignment binds once its right side has run, a ``for`` target
    once its iterable has; the targets of a comprehension stay in it, except a ``:=``. ``x: T`` with no value
    binds ``x`` in a function (``local``) and nowhere else."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        _bind_import(node, names, path)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        _note(names, node.name, _end(node), None, path=path)
        for part in (*_outer_parts(node), *([] if postponed else _annotations(node))):
            _bind_walruses(part, names, (), path=path)
    elif isinstance(node, (ast.Lambda, *COMPREHENSIONS)):
        _bind_walruses(node, names, (), path=path)
    elif isinstance(node, ast.AnnAssign) and node.value is None and not local:
        if not postponed:
            _bind(node.annotation, names, local=local, path=path)
    else:
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            at = _end(node)
        _bind_target(node, names, at, path)
        arms = _child_arms(node)
        for child in _run_children(node, local or postponed):
            after = _end(node.iter) if isinstance(node, (ast.For, ast.AsyncFor)) and child is node.target else at
            arm = arms.get(id(child))
            _bind(child, names, after, local=local, postponed=postponed, path=(*path, arm) if arm else path)


def _run_children(node: ast.AST, skip_annotation: bool) -> list[ast.AST]:
    """The children of ``node``, without the annotation of an ``x: T = v`` that never evaluates it."""
    skip = node.annotation if skip_annotation and isinstance(node, ast.AnnAssign) else None
    return [child for child in ast.iter_child_nodes(node) if child is not skip]


def _bind_comprehension(node: ast.AST, names: Names, unseen: tuple[Span, ...], lift: tuple[Span, ...],
                        path: Arms) -> None:
    gens = node.generators  # type: ignore[attr-defined]
    elts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]  # type: ignore[attr-defined]
    own = (elts[0].lineno, elts[0].col_offset), _end(elts[-1])
    here = (*lift, own)
    _bind_walruses(gens[0].iter, names, unseen, here, path)
    if isinstance(node, ast.GeneratorExp):
        return
    for gen in gens:
        for part in ([] if gen is gens[0] else [gen.iter]) + gen.ifs:
            _bind_walruses(part, names, unseen, here, path)
    ran_first = tuple(_span(part) for gen in gens for part in (gen.iter, *gen.ifs))
    for part in elts:
        _bind_walruses(part, names, (*unseen, *ran_first), lift, path)


def _bind_walruses(node: ast.AST, names: Names, unseen: tuple[Span, ...], lift: tuple[Span, ...] = (),
                   path: Arms = ()) -> None:
    """A ``:=`` in a comprehension binds in the scope around it, though not in a lambda's body. The element
    of a comprehension runs after its iterables and filters, whatever the text order: ``unseen`` holds
    those, where the element's binding is not in effect. A ``:=`` in an iterable or filter is in effect
    for the element, which stands before it in the text: ``lift`` holds the spans of the elements around
    it, the binding takes effect where each starts, and not in the text from its end to the ``:=``.
    A generator expression runs only its first iterable where it stands, so the ``:=`` in the rest of it
    binds nothing here."""
    if isinstance(node, ast.Lambda):
        for part in _outer_parts(node):
            _bind_walruses(part, names, unseen, lift, path)
        return
    if isinstance(node, COMPREHENSIONS):
        _bind_comprehension(node, names, unseen, lift, path)
        return
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        for start, end in lift or ((_end(node), (0, 0)),):
            _note(names, node.target.id, start, None, (*unseen, (end, _end(node))) if lift else unseen, path)
    for child in ast.iter_child_nodes(node):
        _bind_walruses(child, names, unseen, lift, path)


def _bind_target(node: ast.AST, names: Names, at: Pos | None, path: Arms) -> None:
    if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
        _note(names, node.id, at or _end(node), None, path=path)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        _note(names, node.name, (node.lineno, node.col_offset), None, path=path)
    else:
        _bind_pattern(node, names, at, path)


def _bind_pattern(node: ast.AST, names: Names, at: Pos | None, path: Arms) -> None:
    """The name a ``match`` capture binds: ``case x``, ``case [*x]``, ``case {**x}``; ``_`` has none."""
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        name = node.name
    elif isinstance(node, ast.MatchMapping):
        name = node.rest
    else:
        return
    if name:
        _note(names, name, at or (node.lineno, node.col_offset), None, path=path)


def _resolve(call: ast.Call, scopes: list[Scope]) -> str | None:
    """``"time.sleep"`` or ``"threading.Timer"`` when ``call`` is one, by the innermost binding of its name.
    In the scope the call stands in, that is the last binding before the call; in a scope around a
    function, the last binding of the scope. Where branches meet, a name may mean several things (one
    import on Windows, another elsewhere): the call counts when any of them is a sleep or timer."""
    pos = (call.lineno, call.col_offset)

    def meanings(name: str) -> list[str | None]:
        by_position = True
        for scope in scopes:
            entries = scope.names.get(name)
            if entries:
                seen = [e for e in entries
                        if not by_position or (e[0] <= pos and not any(a <= pos < b for a, b in e[2]))]
                if seen:
                    return _survivors(seen, pos)
                if not scope.is_class:
                    return []
            by_position = by_position and (scope.is_comp or scope.is_class)
        return []

    func = call.func
    if isinstance(func, ast.Name):
        return next((m for m in meanings(func.id) if m and "." in m), None)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        for module in meanings(func.value.id):
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


def _annotations(node: ast.AST) -> list[ast.AST]:
    """The parameter and return annotations of a ``def``, which run where it is defined (before Python 3.14,
    and unless ``from __future__ import annotations`` postpones them)."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    a = node.args
    found = [arg.annotation for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg)
             if arg is not None]
    return [part for part in (*found, node.returns) if part is not None]


class _Walker:
    """Finds the calls that resolve to a sleep or timer. A name means what the innermost scope that binds
    it says; a class body is not visible from the functions inside it."""

    def __init__(self, postponed: bool = False) -> None:
        self.postponed = postponed  # ``from __future__ import annotations``: annotations never run
        self.found: list[tuple[ast.Call, str]] = []
        self.done: set[int] = set()  # the first iterable of a comprehension, walked in the outer scope
        self.in_function = False  # ``x: T`` in a function body never evaluates ``T``

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
        for child in _annotations(node) if not self.postponed else []:
            self.walk(child, scopes)
        inner = [Scope(_own_names(node, self.postponed), isinstance(node, ast.ClassDef), False)]
        inner += [s for s in scopes if not s.is_class]
        body = [node.body] if isinstance(node, ast.Lambda) else node.body  # type: ignore[attr-defined]
        outer, self.in_function = self.in_function, not isinstance(node, ast.ClassDef)
        for child in body:
            self.walk(child, inner)
        self.in_function = outer

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
            if isinstance(node, ast.AnnAssign) and child is node.annotation and (self.postponed or self.in_function):
                continue
            self.walk(child, scopes)


def _calls(tree: ast.Module) -> list[tuple[ast.Call, str]]:
    """Every call that resolves to a sleep or timer, with its target."""
    postponed = any(isinstance(n, ast.ImportFrom) and n.module == "__future__" and n.level == 0
                    and any(alias.name == "annotations" for alias in n.names) for n in tree.body)
    walker = _Walker(postponed)
    walker.walk(tree, [Scope(_own_names(tree, postponed), False, False)])
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
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise ValueError('expected {"files": {"<file>": <count>}}')
    if not all(type(v) is int for v in files.values()):
        raise ValueError("a count is not an integer")
    return dict(files)


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
