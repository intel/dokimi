# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fail a PR that narrows an existing test without saying why.

A test the PR changes is compared, function by function, with the same test at the
PR base. It is narrowed when the PR removes it (or its file), lowers its count of
checks (``assert`` statements, ``assert*`` calls, ``pytest.raises``/``warns``), adds a
skip or xfail, drops a ``match=`` from ``pytest.raises``, widens one to ``Exception``
or ``BaseException``, raises a tolerance (``rel``, ``abs``, ``rtol``, ``atol``,
``rel_tol``, ``abs_tol``), or cuts ``parametrize`` cases (the product of stacked decorators). A skip or xfail
mark counts wherever it sits: a decorator, ``pytest.param(marks=...)``, a class
decorator or ``pytestmark``. A file the runner no longer collects counts as deleted. New tests are not read, and
a removed test whose body (signature and decorators included) reappears as a test that
the PR adds to a test file was renamed or moved, not removed, once per added test. A speed bump, not a proof: ``assert x == y`` turned
into ``assert x`` keeps the count.

A narrowing passes when the PR says why: a ``Test-narrowed: <reason>`` line in a
commit message of ``base..head`` waives the findings in the files that commit
touches (a merge included), and a ``# Test-narrowed: <reason>`` comment the PR adds
inside a test function (decorators included) waives that function's. A test file is
``test_*.py`` or ``*_test.py`` under a ``tests/`` directory, outside ``fixtures/``.

    python scripts/check_test_narrowing.py --base origin/main --head HEAD

Exits 1 after an ``::error file=...`` line per narrowed test, 2 when git cannot diff
``--base``/``--head``.
"""

from __future__ import annotations

import argparse
import ast
import copy
import io
import math
import re
import subprocess
import sys
import tokenize
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard

REPO = Path(__file__).resolve().parents[2]
WAIVER = re.compile(r"^\s*(?:#[ \t]*)?Test-narrowed:[ \t]*(\S.*)$", re.IGNORECASE | re.MULTILINE)
TOLERANCES = {"rel", "abs", "rtol", "atol", "rel_tol", "abs_tol"}
RAISES = {"raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"}
SKIPS = {"skip", "skipif", "xfail", "skipTest", "skipIf", "skipUnless", "expectedFailure"}
BROAD = {"Exception", "BaseException"}
# What a tolerance is when its keyword is left out of pytest.approx or math.isclose.
DEFAULTS = {"rel": 1e-6, "abs": 1e-12, "rtol": 1e-5, "atol": 1e-8, "rel_tol": 1e-9, "abs_tol": 0.0}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)


def _checked_git(*args: str) -> str:
    result = _git(*args)
    result.check_returncode()
    return result.stdout


def is_test_file(path: str) -> bool:
    """Whether ``path`` (repository-relative, ``/``-separated) holds tests this check reads."""
    parts = path.split("/")
    name = parts[-1]
    return (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))
            and "tests" in parts[:-1] and "fixtures" not in parts[:-1])


@dataclass
class Shape:
    """What a test function checks, as far as narrowing goes."""

    line: int
    end: int
    checks: int = 0
    skips: int = 0
    matches: int = 0
    broad: int = 0
    tolerances: dict[str, float] = field(default_factory=dict)
    cases: int | None = None
    cases_known: bool = True
    body: str = ""


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _exc_names(node: ast.expr) -> set[str]:
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    return {_name(item) for item in items}


def _read_raises(call: ast.Call, shape: Shape) -> None:
    pattern = {k.arg: k.value for k in call.keywords}.get("match")
    unset = isinstance(pattern, ast.Constant) and pattern.value is None
    if (pattern is not None and not unset) or _name(call.func).endswith("Regex"):
        shape.matches += 1
    if call.args and _exc_names(call.args[0]) & BROAD:
        shape.broad += 1


def _skip_weight(call: ast.Call) -> int:
    """0 for a skip condition that is the constant that never skips, else 1."""
    name = _name(call.func)
    if name in {"skipif", "skipIf", "skipUnless"} and call.args and isinstance(call.args[0], ast.Constant):
        return int(bool(call.args[0].value) == (name != "skipUnless"))
    return 1


def _skips_in(node: ast.AST) -> int:
    """Skip and xfail marks under ``node``, a bare ``pytest.mark.skip`` included."""
    called = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    total = 0
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and _name(sub.func) in SKIPS:
            total += _skip_weight(sub)
        elif isinstance(sub, (ast.Attribute, ast.Name)) and id(sub) not in called and _name(sub) in SKIPS:
            total += 1
    return total


def _read_tolerances(call: ast.Call, shape: Shape) -> None:
    for kw in call.keywords:
        if kw.arg in TOLERANCES and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            if value is None:
                number = math.inf
            elif isinstance(value, (int, float)):
                number = float(value)
            else:
                continue
            shape.tolerances[kw.arg] = max(shape.tolerances.get(kw.arg, 0.0), number)


def _read_call(call: ast.Call, shape: Shape) -> None:
    name = _name(call.func)
    if name in RAISES:
        shape.checks += 1
        _read_raises(call, shape)
    elif name.startswith("assert"):
        shape.checks += 1
    elif name in SKIPS:
        shape.skips += _skip_weight(call)
    _read_tolerances(call, shape)


def _cases(decorator: ast.expr) -> tuple[bool, int | None]:
    """(is a parametrize, its case count or None when the values are not a literal list)."""
    if not (isinstance(decorator, ast.Call) and _name(decorator.func) == "parametrize"):
        return False, None
    values = decorator.args[1] if len(decorator.args) > 1 else {
        k.arg: k.value for k in decorator.keywords}.get("argvalues")
    if isinstance(values, (ast.List, ast.Tuple)):
        return True, len(values.elts)
    return True, None


def shape_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Shape:
    """The checks, skips, tolerances and parametrize cases of one test function."""
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    shape = Shape(line=first, end=node.end_lineno or node.lineno,
                  body="|".join(ast.dump(n) for n in [node.args, *node.decorator_list, *node.body]))
    for decorator in node.decorator_list:
        parametrized, cases = _cases(decorator)
        if parametrized and cases is None:
            shape.cases_known = False
        elif cases is not None:
            shape.cases = cases if shape.cases is None else shape.cases * cases
        shape.skips += _skips_in(decorator)
        for sub in ast.walk(decorator):
            if isinstance(sub, ast.Call):
                _read_tolerances(sub, shape)
    for stmt in node.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Assert):
                shape.checks += 1
            elif isinstance(sub, ast.Call):
                _read_call(sub, shape)
    return shape


def _is_test(node: ast.AST) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def _is_test_class(node: ast.AST) -> TypeGuard[ast.ClassDef]:
    return isinstance(node, ast.ClassDef) and (
        node.name.startswith("Test") or any(_name(base).endswith("TestCase") for base in node.bases))


def _pytestmark_skips(body: list[ast.stmt]) -> int:
    return sum(_skips_in(stmt.value) for stmt in body if isinstance(stmt, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets))


def _inline_constants(tree: ast.Module) -> ast.Module:
    """``tree`` with each read of a module-level constant replaced by a copy of its value.

    A name assigned more than once, or read in its own value, is left alone, and so is a
    name a function binds itself (parameter or local)."""
    targets = [t.id for node in tree.body if isinstance(node, ast.Assign)
               for t in node.targets if isinstance(t, ast.Name)]
    consts = {node.targets[0].id: node.value for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and targets.count(node.targets[0].id) == 1
              and not any(isinstance(n, ast.Name) and n.id == node.targets[0].id
                          for n in ast.walk(node.value))}

    class Inline(ast.NodeTransformer):
        def __init__(self) -> None:
            self.shadowed: set[str] = set()

        def visit_Name(self, node: ast.Name) -> ast.AST:
            if isinstance(node.ctx, ast.Load) and node.id in consts and node.id not in self.shadowed:
                return copy.deepcopy(consts[node.id])
            return node

        def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
            node.decorator_list = [self.visit(d) for d in node.decorator_list]
            local = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
            local |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                      and isinstance(n.ctx, (ast.Store, ast.Del))}
            outer, self.shadowed = self.shadowed, self.shadowed | local
            node.body = [self.visit(stmt) for stmt in node.body]
            self.shadowed = outer
            return node

        visit_FunctionDef = visit_AsyncFunctionDef = _function

    return Inline().visit(tree)


def tests_in(text: str) -> dict[str, Shape] | None:
    """``Class.test`` or ``test`` name to shape of each test in ``text``; None if it does not parse."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    module_skips = _pytestmark_skips(tree.body)
    tree = _inline_constants(tree)
    found: dict[str, Shape] = {}
    for node in tree.body:
        if _is_test(node):
            found[node.name] = shape_of(node)
            found[node.name].skips += module_skips
        elif _is_test_class(node):
            class_skips = (module_skips + sum(_skips_in(d) for d in node.decorator_list)
                           + _pytestmark_skips(node.body))
            for item in node.body:
                if _is_test(item):
                    shape = found[f"{node.name}.{item.name}"] = shape_of(item)
                    shape.skips += class_skips
    return found


def _loosened(base: dict[str, float], head: dict[str, float]) -> bool:
    return any(kind not in head and DEFAULTS[kind] > value for kind, value in base.items()) or any(
        value > base.get(kind, DEFAULTS[kind]) for kind, value in head.items())


def narrowing(base: Shape, head: Shape) -> list[str]:
    """How ``head`` checks less than ``base``; empty when it does not."""
    found = []
    if head.checks < base.checks:
        found.append(f"fewer checks ({base.checks} -> {head.checks})")
    if head.skips > base.skips:
        found.append("adds a skip or xfail")
    if head.matches < base.matches:
        found.append("drops a match= from pytest.raises")
    if head.broad > base.broad:
        found.append("widens pytest.raises to Exception/BaseException")
    if _loosened(base.tolerances, head.tolerances):
        found.append("loosens a tolerance")
    if (base.cases is not None and base.cases_known and head.cases_known
            and (head.cases or 0) < base.cases):
        found.append(f"cuts parametrize cases ({base.cases} -> {head.cases or 0})")
    return found


def _comments(text: str, shape: Shape) -> list[str]:
    """The Python comments of the test at ``shape``, the indented ones after its last statement too."""
    lines = text.split("\n")
    end = shape.end
    # The AST ends a function at its last statement, so take the indented comments after it.
    while end < len(lines) and lines[end][:1].isspace() and lines[end].lstrip().startswith("#"):
        end += 1
    try:
        return [tok.string for tok in tokenize.generate_tokens(io.StringIO(text).readline)
                if tok.type == tokenize.COMMENT and shape.line <= tok.start[0] <= end]
    except (tokenize.TokenError, SyntaxError):
        return []


def _waived_inline(text: str, shape: Shape, base_text: str, base_shape: Shape) -> bool:
    old = set(_comments(base_text, base_shape))
    return any(WAIVER.search(comment) for comment in _comments(text, shape) if comment not in old)


def compare(base_text: str, head_text: str | None,
            moved: Counter[str] | None = None) -> list[tuple[str, int, str]]:
    """(test, head line, what narrowed) for each test of ``base_text`` that ``head_text`` narrows.

    A test gone from ``head_text`` whose body is in ``moved`` was renamed or moved, not removed;
    each entry of ``moved`` accounts for one removed test."""
    moved = Counter() if moved is None else moved
    before = tests_in(base_text) or {}
    after = tests_in(head_text) if head_text is not None else {}
    if after is None:
        return []
    found = []
    for name, shape in before.items():
        now = after.get(name)
        if now is None:
            if moved[shape.body] > 0:
                moved[shape.body] -= 1
            else:
                found.append((name, 0, "removed"))
            continue
        reasons = narrowing(shape, now)
        if reasons and not _waived_inline(head_text or "", now, base_text, shape):
            found.append((name, now.line, "; ".join(reasons)))
    return found


def changed_tests(base: str, head: str) -> list[tuple[str, str | None, str | None]]:
    """(path shown, path at base or None if added, path at head or None if deleted) per test file.

    A file that became a symlink is deleted as far as the runner is concerned."""
    fields = _checked_git("diff", "-z", "-M", "--name-status", "--diff-filter=AMRDT",
                          f"{base}...{head}").split("\0")[:-1]
    out: list[tuple[str, str | None, str | None]] = []
    i = 0
    while i < len(fields):
        status, new = fields[i], None
        if status.startswith("R"):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old = fields[i + 1]
            new = None if status[0] in "DT" else old
            i += 2
        if is_test_file(old) or is_test_file(new or ""):
            out.append((new or old, None if status == "A" else old, new))
    return out


def waivers(base: str, head: str) -> dict[str, str]:
    """Path to reason for each file a commit of ``base..head`` touches whose message has a
    ``Test-narrowed:`` line: the line waives findings in those files only."""
    waived: dict[str, str] = {}
    for sha in _checked_git("rev-list", f"{base}..{head}").split():
        reasons = [m.group(1).strip() for m in WAIVER.finditer(_checked_git("log", "-1", "--format=%B", sha))]
        if reasons:
            names = _checked_git("diff-tree", "--no-commit-id", "--name-only", "-z", "-r", "-m", "--root", sha)
            waived.update(dict.fromkeys(names.split("\0"), "; ".join(reasons)))
    waived.pop("", None)
    return waived


def findings(base: str, head: str) -> list[tuple[str, str, int, str]]:
    """(path, test, line, what narrowed) of every test the PR narrows without an inline waiver."""
    merge_base = _checked_git("merge-base", base, head).strip()
    files = changed_tests(base, head)
    heads = {new: _checked_git("show", f"{head}:{new}") for _, _, new in files
             if new and is_test_file(new)}
    bases = {old: _checked_git("show", f"{merge_base}:{old}") for _, old, _ in files
             if old and is_test_file(old)}
    existing = {(new, name) for _, old, new in files if old in bases and new in heads
                for name in tests_in(bases[old]) or {}}
    moved: Counter[str] = Counter(
        shape.body for new, text in heads.items()
        for name, shape in (tests_in(text) or {}).items() if (new, name) not in existing)
    found = []
    for shown, old, new in files:
        if old not in bases:
            continue
        found += [(shown, *item) for item in compare(bases[old], heads.get(new or ""), moved)]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        found = findings(args.base, args.head)
        waived = waivers(args.base, args.head) if found else {}
    except subprocess.CalledProcessError as exc:
        print(f"cannot diff {args.base}...{args.head}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    for path, test, _, _ in found:
        if path in waived:
            print(f"{path}::{test} narrowed, waived by a commit: {waived[path]}")
    found = [item for item in found if item[0] not in waived]
    for path, test, line, what in found:
        where = f",line={line}" if line else ""
        print(f"::error file={path}{where},title=Test narrowed::{test}: {what}; say why with a "
              "'Test-narrowed: <reason>' commit line or comment in the test (AGENTS.md)")
    if found:
        return 1
    print("No existing test narrowed by this PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
