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
decorator or ``pytestmark``, and so does a ``pytest.skip``/``pytest.importorskip`` call
in the test or at the top of its module. Tolerances are compared per call; a new call
with a tolerance is a new check only when the test's count of checks or cases grew. A file the
runner no longer collects counts as deleted, and a ``conftest.py`` statement the PR adds
or changes that names ``collect_ignore``, ``collect_ignore_glob`` or ``pytest_ignore_collect``
is a finding of its own. New tests are not read, and
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
SKIPS = {"skip", "skipif", "xfail", "skipTest", "skipIf", "skipUnless", "expectedFailure", "importorskip"}
# The skips that run where they are called, as opposed to marks a decorator applies.
EFFECTS = {"skip", "xfail", "skipTest", "importorskip"}
TOLERANT = {"approx", "isclose", "allclose", "assert_allclose"}
IGNORES = re.compile(r"collect_ignore(_glob)?|pytest_ignore_collect")
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


def is_conftest(path: str) -> bool:
    """Whether ``path`` is a ``conftest.py`` the runner reads, so it can stop collecting tests."""
    parts = path.split("/")
    return parts[-1] == "conftest.py" and "fixtures" not in parts[:-1]


@dataclass
class Shape:
    """What a test function checks, as far as narrowing goes."""

    line: int
    end: int
    checks: int = 0
    skips: int = 0
    matches: int = 0
    broad: int = 0
    tolerances: list[tuple[str, dict[str, float]]] = field(default_factory=list)
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
    condition = call.args[0] if call.args else {k.arg: k.value for k in call.keywords}.get("condition")
    if name in {"skipif", "skipIf", "skipUnless"} and isinstance(condition, ast.Constant):
        return int(bool(condition.value) == (name != "skipUnless"))
    return 1


def _is_mark(func: ast.expr) -> bool:
    owner = func.value if isinstance(func, ast.Attribute) else None
    return _name(owner) == "mark" if owner is not None else False


def _effects(node: ast.AST) -> int:
    """Skip, xfail and importorskip calls under ``node`` that skip where they run."""
    return sum(1 for sub in ast.walk(node) if isinstance(sub, ast.Call)
               and _name(sub.func) in EFFECTS and not _is_mark(sub.func))


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
    """Record ``call`` with its tolerances when it is a comparison that takes them."""
    named = [kw for kw in call.keywords if kw.arg in TOLERANCES]
    if not named and _name(call.func) not in TOLERANT:
        return
    tolerances: dict[str, float] = {}
    for kw in named:
        value = kw.value.value if isinstance(kw.value, ast.Constant) else ""
        if value is None or isinstance(value, (int, float)):
            tolerances[str(kw.arg)] = math.inf if value is None else float(value)
    others = [kw for kw in call.keywords if kw not in named]
    shape.tolerances.append((ast.dump(ast.Call(call.func, call.args, others)), tolerances))


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


def _targets(stmt: ast.stmt) -> list[ast.expr]:
    if isinstance(stmt, ast.Assign):
        return stmt.targets
    return [stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else []


def _scope_skips(body: list[ast.stmt]) -> int:
    """The skips each test of a module or class body inherits: the ``pytestmark`` marks and
    the skip calls that run on import."""
    total = 0
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in _targets(stmt)):
            total += _skips_in(stmt)
        else:
            total += _effects(stmt)
    return total


def _inline_constants(tree: ast.Module) -> ast.Module:
    """``tree`` with each read of a module-level constant replaced by a copy of its value.

    A name assigned more than once, read in its own value or bound to a call that skips
    (``pytest.importorskip``), is left alone, and so is a name a function binds itself
    (parameter or local)."""
    targets = [t.id for node in tree.body for t in _targets(node) if isinstance(t, ast.Name)]
    consts = {node.targets[0].id: node.value for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and targets.count(node.targets[0].id) == 1
              and not _effects(node.value)
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
    tree = _inline_constants(tree)
    module_skips = _scope_skips(tree.body)
    found: dict[str, Shape] = {}
    for node in tree.body:
        if _is_test(node):
            found[node.name] = shape_of(node)
            found[node.name].skips += module_skips
        elif _is_test_class(node):
            class_skips = (module_skips + sum(_skips_in(d) for d in node.decorator_list)
                           + _scope_skips(node.body))
            for item in node.body:
                if _is_test(item):
                    shape = found[f"{node.name}.{item.name}"] = shape_of(item)
                    shape.skips += class_skips
    return found


def _loosened(base: dict[str, float], head: dict[str, float]) -> bool:
    return any(kind not in head and DEFAULTS[kind] > value for kind, value in base.items()) or any(
        value > base.get(kind, DEFAULTS[kind]) for kind, value in head.items())


Calls = list[tuple[str, dict[str, float]]]


def _paired(base: Calls, head: Calls) -> tuple[list[tuple[dict[str, float], dict[str, float]]], Calls]:
    """Base and head tolerance calls paired one-to-one: the same call with the same tolerances
    first, then the same call, then in order; and the head calls left over."""
    left, right = list(base), list(head)
    pairs: list[tuple[dict[str, float], dict[str, float]]] = []
    for same in (lambda a, b: a == b, lambda a, b: a[0] == b[0], lambda a, b: True):
        for call in list(left):
            twin = next((other for other in right if same(call, other)), None)
            if twin is not None:
                left.remove(call)
                right.remove(twin)
                pairs.append((call[1], twin[1]))
    return pairs, right


def _tolerance_loosened(base: Shape, head: Shape) -> bool:
    """A paired call loosens, or a call left over loosens on the defaults while the test has
    no more checks or cases: then it rewrote a check rather than adding one."""
    pairs, extra = _paired(base.tolerances, head.tolerances)
    if head.checks <= base.checks and (head.cases or 1) <= (base.cases or 1):
        pairs += [({}, tolerances) for _, tolerances in extra]
    return any(_loosened(old, new) for old, new in pairs)


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
    if _tolerance_loosened(base, head):
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


def _ignore_statements(text: str) -> list[tuple[str, ast.stmt]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [stmt.name]
        else:
            names = [n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)]
        found += [(name, stmt) for name in dict.fromkeys(names) if IGNORES.fullmatch(name)]
    return found


def new_ignores(base_text: str, head_text: str) -> list[tuple[str, int, str]]:
    """(name, head line, what) for each top-level ``collect_ignore``, ``collect_ignore_glob`` or
    ``pytest_ignore_collect`` statement of a conftest that the PR adds or changes."""
    old = {ast.dump(stmt) for _, stmt in _ignore_statements(base_text)}
    return [(name, stmt.lineno, "may stop collecting tests") for name, stmt in _ignore_statements(head_text)
            if ast.dump(stmt) not in old]


def changed_tests(base: str, head: str) -> list[tuple[str, str | None, str | None]]:
    """(path shown, path at base or None if added, path at head or None if deleted) per test
    file or ``conftest.py``.

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
        if any(wanted(path) for path in (old, new or "") for wanted in (is_test_file, is_conftest)):
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
    """(path, test, line, what narrowed) of every test the PR narrows without an inline waiver,
    and of every collection ignore it adds to a ``conftest.py``."""
    merge_base = _checked_git("merge-base", base, head).strip()
    files = changed_tests(base, head)
    heads = {new: _checked_git("show", f"{head}:{new}") for _, _, new in files
             if new and is_test_file(new)}
    bases = {old: _checked_git("show", f"{merge_base}:{old}") for _, old, _ in files
             if old and is_test_file(old)}
    existing = {(new, name) for _, old, new in files if old in bases and new in heads
                for name in tests_in(bases[old]) or {}}
    moved: Counter[str] = Counter(
        shape.body for path, text in heads.items()
        for name, shape in (tests_in(text) or {}).items() if (path, name) not in existing)
    found = []
    for shown, old, new in files:
        if old in bases:
            found += [(shown, *item) for item in compare(bases[old], heads.get(new or ""), moved)]
    return found + _conftest_findings(merge_base, head, files)


def _conftest_findings(merge_base: str, head: str,
                       files: list[tuple[str, str | None, str | None]]) -> list[tuple[str, str, int, str]]:
    found = []
    for shown, old, new in files:
        if new and is_conftest(new):
            base_text = _checked_git("show", f"{merge_base}:{old}") if old and is_conftest(old) else ""
            found += [(shown, *item) for item in new_ignores(base_text, _checked_git("show", f"{head}:{new}"))]
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
