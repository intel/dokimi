#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fail a PR that narrows an existing test without saying why.

A test the PR changes is compared, function by function, with the same test at the
PR base. It is narrowed when the PR removes it (or its file), lowers its count of
checks (``assert`` statements, ``assert*`` calls, ``pytest.raises``/``warns``), adds a
skip or xfail, drops a ``match=`` from ``pytest.raises``, widens one to ``Exception``
or ``BaseException``, raises a tolerance (``rel``, ``abs``, ``rtol``, ``atol``,
``rel_tol``, ``abs_tol``), or cuts ``parametrize`` cases. New tests are not read.

A narrowing passes when the PR says why: a ``Test-narrowed: <reason>`` line in any
commit message of ``base..head`` waives every finding, and a
``# Test-narrowed: <reason>`` comment inside a test function (decorators included)
waives that function's. A test file is ``test_*.py`` or ``*_test.py`` under a
``tests/`` directory, outside ``fixtures/``.

    python scripts/check_test_narrowing.py --base origin/main --head HEAD

Exits 1 after an ``::error file=...`` line per narrowed test, 2 when git cannot diff
``--base``/``--head``.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard

REPO = Path(__file__).resolve().parents[2]
WAIVER = re.compile(r"^\s*(?:#\s*)?Test-narrowed:\s*(\S.*)$", re.IGNORECASE | re.MULTILINE)
TOLERANCES = {"rel", "abs", "rtol", "atol", "rel_tol", "abs_tol"}
RAISES = {"raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"}
SKIPS = {"skip", "skipif", "xfail", "skipTest", "skipIf", "skipUnless", "expectedFailure"}
BROAD = {"Exception", "BaseException"}


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
    tolerances: list[float] = field(default_factory=list)
    cases: int | None = None


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _exc_names(node: ast.expr) -> set[str]:
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    return {_name(item) for item in items}


def _read_raises(call: ast.Call, shape: Shape) -> None:
    keywords = {k.arg for k in call.keywords}
    if "match" in keywords or _name(call.func).endswith("Regex"):
        shape.matches += 1
    if call.args and _exc_names(call.args[0]) & BROAD:
        shape.broad += 1


def _read_call(call: ast.Call, shape: Shape) -> None:
    name = _name(call.func)
    if name in RAISES:
        shape.checks += 1
        _read_raises(call, shape)
    elif name.startswith("assert"):
        shape.checks += 1
    elif name in SKIPS:
        shape.skips += 1
    for kw in call.keywords:
        if kw.arg in TOLERANCES and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, (int, float)):
                shape.tolerances.append(float(kw.value.value))


def _cases(decorator: ast.expr) -> int | None:
    if (isinstance(decorator, ast.Call) and _name(decorator.func) == "parametrize"
            and len(decorator.args) > 1 and isinstance(decorator.args[1], (ast.List, ast.Tuple))):
        return len(decorator.args[1].elts)
    return None


def shape_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Shape:
    """The checks, skips, tolerances and parametrize cases of one test function."""
    first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    shape = Shape(line=first, end=node.end_lineno or node.lineno)
    for decorator in node.decorator_list:
        cases = _cases(decorator)
        if cases is not None:
            shape.cases = (shape.cases or 0) + cases
        if _name(decorator.func if isinstance(decorator, ast.Call) else decorator) in SKIPS:
            shape.skips += 1
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            shape.checks += 1
        elif isinstance(sub, ast.Call) and sub not in node.decorator_list:
            _read_call(sub, shape)
    return shape


def _is_test(node: ast.AST) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def tests_in(text: str) -> dict[str, Shape] | None:
    """``Class.test`` or ``test`` name to shape of each test in ``text``; None if it does not parse."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    found: dict[str, Shape] = {}
    for node in tree.body:
        if _is_test(node):
            found[node.name] = shape_of(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if _is_test(item):
                    found[f"{node.name}.{item.name}"] = shape_of(item)
    return found


def _loosened(base: list[float], head: list[float]) -> bool:
    if not head:
        return False
    return not base or max(head) > max(base)


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
    if base.cases is not None and (head.cases or 0) < base.cases:
        found.append(f"cuts parametrize cases ({base.cases} -> {head.cases or 0})")
    return found


def _waived_inline(text: str, shape: Shape) -> bool:
    lines = text.split("\n")
    end = shape.end
    # The AST ends a function at its last statement, so take the indented comments after it.
    while end < len(lines) and lines[end][:1].isspace() and lines[end].lstrip().startswith("#"):
        end += 1
    lines = lines[shape.line - 1:end]
    return any("#" in line and WAIVER.search(line[line.index("#"):]) for line in lines)


def compare(base_text: str, head_text: str | None) -> list[tuple[str, int, str]]:
    """(test, head line, what narrowed) for each test of ``base_text`` that ``head_text`` narrows."""
    before = tests_in(base_text) or {}
    after = tests_in(head_text) if head_text is not None else {}
    if after is None:
        return []
    found = []
    for name, shape in before.items():
        now = after.get(name)
        if now is None:
            found.append((name, 0, "removed"))
            continue
        reasons = narrowing(shape, now)
        if reasons and not _waived_inline(head_text or "", now):
            found.append((name, now.line, "; ".join(reasons)))
    return found


def changed_tests(base: str, head: str) -> list[tuple[str, str, str | None]]:
    """(path shown, path at base, path at head or None if deleted) of each changed test file."""
    fields = _checked_git("diff", "-z", "-M", "--name-status", "--diff-filter=MRD",
                          f"{base}...{head}").split("\0")[:-1]
    out: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields):
        status, new = fields[i], None
        if status.startswith("R"):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            old = fields[i + 1]
            new = None if status == "D" else old
            i += 2
        if is_test_file(old):
            out.append((new or old, old, new))
    return out


def waivers(base: str, head: str) -> list[str]:
    """The reasons of the ``Test-narrowed:`` lines in the commit messages of ``base..head``."""
    return [m.group(1).strip() for m in WAIVER.finditer(_checked_git("log", "--format=%B", f"{base}..{head}"))]


def findings(base: str, head: str) -> list[tuple[str, str, int, str]]:
    """(path, test, line, what narrowed) of every test the PR narrows without an inline waiver."""
    merge_base = _checked_git("merge-base", base, head).strip()
    found = []
    for shown, old, new in changed_tests(base, head):
        base_text = _checked_git("show", f"{merge_base}:{old}")
        head_text = _checked_git("show", f"{head}:{new}") if new else None
        found += [(shown, *item) for item in compare(base_text, head_text)]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        found = findings(args.base, args.head)
        reasons = waivers(args.base, args.head) if found else []
    except subprocess.CalledProcessError as exc:
        print(f"cannot diff {args.base}...{args.head}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    if found and reasons:
        print(f"{len(found)} narrowed test(s) waived by commit messages: " + " | ".join(reasons))
        return 0
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
