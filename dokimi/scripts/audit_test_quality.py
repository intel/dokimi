#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Find tests that cannot fail for a wrong behaviour.

A static scan (``ast``; nothing is imported or run) of every ``test_*``
function under ``tests/``. Coverage counts a line as tested once a test runs
it; these flags name the tests whose checks would still pass if the code under
test returned the wrong value:

- ``type_only``: every ``assert`` is ``x is not None``, ``callable``,
  ``hasattr``, ``isinstance(x, <builtin>)`` or ``len(x) > 0``. ``is None``,
  bare truthiness, a negation and ``isinstance`` against a dokimi class are
  outcomes (an absence, a flag, the class a dispatcher picked), so they pass.
- ``mock_echo``: the test patches something and checks only *that* a mock was
  called (``assert_called*``, ``.called``, ``.call_count``), never with what
  (``assert_called_with`` and ``call_args`` carry the behaviour).
- ``set_then_read``: every value assert reads ``x.attr == LIT`` straight off
  ``x = Call(attr=LIT)``: the object echoes its constructor.
- ``constant_mirror``: every value assert pins a ``dokimi`` constant or an enum
  ``.value`` to a literal, restating the source.
- ``no_assert``: no assert, ``pytest.raises``/``warns``, ``assert*`` call or
  call to a same-file helper that asserts.
- ``duplicate``: the body, parameters and decorators are identical to an
  earlier test in the same file and class; only the name differs.
- ``trivial_size``: at most three statements and one of the first four flags.
  Reported, not gated.

``tests/test_audit_test_quality.py`` gates the others with a baseline that
only shrinks. A test deliberately shaped like a flag (a must-not-raise smoke
test, a serialized spelling pin) carries ``# test-quality: ok <reason>`` on
its ``def`` or a decorator line.

    python scripts/audit_test_quality.py [--json PATH]

prints the report (``--json`` writes every flagged row) and always exits 0.
"""

from __future__ import annotations

import argparse
import ast
import collections
import itertools
import json
import pathlib
from collections.abc import Callable, Iterable, Iterator
from typing import NamedTuple

PROJECT = pathlib.Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"
PACKAGE = "dokimi"

FLAGS = (
    "type_only", "mock_echo", "set_then_read", "constant_mirror", "no_assert", "duplicate",
    "trivial_size",
)
GATED_FLAGS = FLAGS[:-1]
# trivial_size is derived from these: a short test is only worth reporting when its asserts are weak.
_SHAPE_FLAGS = ("type_only", "mock_echo", "set_then_read", "constant_mirror")
_TRIVIAL_MAX_STATEMENTS = 3
EXEMPT_MARKER = "test-quality: ok"

_WEAK_CALLS = frozenset({"callable", "hasattr"})
_TYPE_CHECK_CALLS = frozenset({"isinstance", "issubclass"})
# isinstance(x, dict) says nothing about x; isinstance(x, OpenAIJudge) says which class was built.
_BUILTIN_TYPES = frozenset(
    {"dict", "list", "tuple", "set", "frozenset", "str", "bytes", "int", "float", "bool",
     "object", "type"}
)
_RAISES_CALLS = frozenset({"raises", "warns", "deprecated_call", "assertRaises", "assertWarns"})
_PATCH_CALLS = frozenset(
    {"patch", "MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock"}
)
# Whether a mock was called, not with what; call_args and mock_calls carry the arguments.
_MOCK_VERIFY_ATTRS = frozenset({"called", "call_count", "await_count", "awaited"})
_MOCK_VERIFY_METHODS = frozenset(
    {"assert_called", "assert_called_once", "assert_not_called", "assert_awaited",
     "assert_awaited_once", "assert_not_awaited"}
)
# len(x) > 0 and its spellings only say "x is non-empty".
_NONEMPTY_LEN = frozenset({(ast.Gt, 0), (ast.GtE, 1), (ast.NotEq, 0)})
_NONEMPTY_LEN_REVERSED = frozenset({(ast.Lt, 0), (ast.LtE, 1), (ast.NotEq, 0)})
# A call whose name holds one of these (check_x, verify_y, _expect_z) counts as a check.
_ASSERTION_HINTS = ("assert", "check", "verify", "expect", "validate", "ensure", "raises",
                    "warns", "fail")
# Builtins that read an object without changing it: passing it to one is not a mutation.
_READ_ONLY_BUILTINS = frozenset(
    {"callable", "hasattr", "getattr", "isinstance", "issubclass", "len", "repr", "str", "print",
     "type", "id"}
)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


class Audit(NamedTuple):
    """One test function and the flags it carries (possibly none)."""

    file: str
    line: int
    test: str
    flags: tuple[str, ...]
    exempt: bool = False

    @property
    def key(self) -> str:
        # No line number, so an edit elsewhere in the file does not move the key.
        return f"{self.file}::{self.test}"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _is_self_call(node: ast.Call) -> bool:
    func = node.func
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self"


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([node.id, *reversed(parts)])


def _own_nodes(body: Iterable[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of a body, not descending into nested functions or classes."""
    stack: list[ast.AST] = [n for n in body if not isinstance(n, _SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _SCOPES))


def _statement_count(body: list[ast.stmt]) -> int:
    first = body[0]
    docstring = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                 and isinstance(first.value.value, str))
    return len(body) - docstring


def _test_functions(tree: ast.Module) -> list[tuple[str, _FuncDef]]:
    """Every ``test_*`` function with its qualified name (``Class.test_x`` for a method)."""
    found: list[tuple[str, _FuncDef]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
                continue
            if isinstance(child, _FuncDef) and child.name.startswith("test_"):
                found.append((prefix + child.name, child))
            walk(child, prefix)

    walk(tree, "")
    return found


def _package_names(tree: ast.Module) -> frozenset[str]:
    """Names the file binds by importing from the package under test."""

    def ours(module: str | None) -> bool:
        return module is not None and module.split(".")[0] == PACKAGE

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and ours(node.module):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names if ours(a.name))
    return frozenset(names)


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_len_call(node: ast.expr) -> bool:
    return (isinstance(node, ast.Call) and _call_name(node) == "len" and len(node.args) == 1
            and not node.keywords)


def _is_weak(node: ast.expr) -> bool:
    """An existence, type or non-emptiness check that compares no value."""
    if isinstance(node, ast.BoolOp):
        # `a or b` passes as soon as one operand does, so one weak operand makes it weak.
        return (any if isinstance(node.op, ast.Or) else all)(_is_weak(v) for v in node.values)
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in _TYPE_CHECK_CALLS and len(node.args) == 2:
            types = node.args[1]
            elts = types.elts if isinstance(types, ast.Tuple) else [types]
            return all(isinstance(t, ast.Name) and t.id in _BUILTIN_TYPES for t in elts)
        return name in _WEAK_CALLS
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op, left, right = node.ops[0], node.left, node.comparators[0]
        if isinstance(op, ast.IsNot) and _is_none(right):
            return True
        if _is_len_call(left) and isinstance(right, ast.Constant):
            return (type(op), right.value) in _NONEMPTY_LEN
        if _is_len_call(right) and isinstance(left, ast.Constant):
            return (type(op), left.value) in _NONEMPTY_LEN_REVERSED
    return False


def _eq_operands(node: ast.expr) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
        return node.left, node.comparators[0]
    return None


def _constructor_inputs(nodes: list[ast.AST]) -> dict[str, set[tuple[str, object, type]]]:
    """``var -> {(kwarg, literal, type)}`` for every ``var = Call(kw=LIT)``.

    A name bound more than once is dropped: which binding an assert reads is not
    decidable without control flow. Also drops names that are mutated via method
    calls or attribute assignments after construction.
    """
    bound: dict[str, set[tuple[str, object, type]]] = {}
    seen: collections.Counter[str] = collections.Counter()
    mutated: set[str] = set()
    for node in nodes:
        # Any call on the object (c.bump(), c.items.append(3)) or taking it as an
        # argument (setattr(c, ...), mutate(c)) may change it, wherever the call sits.
        if isinstance(node, ast.Call):
            root = node.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(node.func, ast.Attribute) and isinstance(root, ast.Name):
                mutated.add(root.id)
            if _call_name(node) not in _READ_ONLY_BUILTINS:
                mutated.update(a.id for a in node.args if isinstance(a, ast.Name))
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            seen[node.target.id] += 1  # a walrus rebind: which binding an assert reads is unknown
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    mutated.add(target.value.id)
                # Track tuple targets
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            seen[elt.id] += 1
                        elif isinstance(elt, ast.Attribute) and isinstance(elt.value, ast.Name):
                            # Mark the object as mutated if an attribute is assigned in a tuple
                            mutated.add(elt.value.id)
        elif isinstance(node, ast.AugAssign):
            # Track augmented assignments (+=, -=, etc.)
            if isinstance(node.target, ast.Attribute) and isinstance(node.target.value, ast.Name):
                mutated.add(node.target.value.id)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            # Track annotated assignments with values
            if isinstance(node.target, ast.Attribute) and isinstance(node.target.value, ast.Name):
                mutated.add(node.target.value.id)

        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            seen[target.id] += 1
            if isinstance(node.value, ast.Call):
                bound[target.id] = {
                    (kw.arg, kw.value.value, type(kw.value.value))
                    for kw in node.value.keywords
                    if kw.arg and isinstance(kw.value, ast.Constant)
                }
    return {name: pairs for name, pairs in bound.items() if seen[name] == 1 and name not in mutated}


def _echoes_input(test: ast.expr, inputs: dict[str, set[tuple[str, object, type]]]) -> bool:
    operands = _eq_operands(test)
    if operands is None:
        return False
    for attr, lit in (operands, operands[::-1]):
        if (isinstance(attr, ast.Attribute) and isinstance(attr.value, ast.Name)
                and isinstance(lit, ast.Constant)):
            return (attr.attr, lit.value, type(lit.value)) in inputs.get(attr.value.id, set())
    return False


def _package_constant(node: ast.expr, names: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names and node.id.isupper()
    if isinstance(node, ast.Attribute):
        root = _dotted(node).split(".")[0]
        return root in names and (node.attr == "value" or node.attr.isupper())
    return False


def _is_constant_mirror(test: ast.expr, names: frozenset[str]) -> bool:
    operands = _eq_operands(test)
    return operands is not None and any(
        _package_constant(a, names) and isinstance(b, ast.Constant)
        for a, b in (operands, operands[::-1])
    )


def _is_patching_call(node: ast.Call) -> bool:
    dotted = _dotted(node.func)
    if dotted == "monkeypatch.setattr" or dotted.endswith(("patch.object", "patch.dict")):
        return True
    return _call_name(node) in _PATCH_CALLS


def _is_mock_verify(test: ast.expr) -> bool:
    """An assert that only reads whether a mock was called.

    ``assert m.call_count == 2 and vote.aggregate == 70.0`` also checks a value,
    so each operand of an ``and`` is judged on its own. An ``or`` passes as soon as
    one operand does, so one mock-only operand makes it mock-only.

    Tuple comparisons like ``assert (m.call_count, out) == (1, 42)`` check both
    the mock call and the output, so they are not mock-only. But
    ``assert (m.call_count, m.called) == (1, True)`` is mock-only.
    """
    if isinstance(test, ast.BoolOp):
        if isinstance(test.op, ast.And):
            return all(map(_is_mock_verify, test.values))
        elif isinstance(test.op, ast.Or):
            return any(map(_is_mock_verify, test.values))
    # If this is a comparison with tuples, check if all tuple elements are mock attributes
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
        left_tuple = isinstance(test.left, ast.Tuple)
        right_tuple = isinstance(test.comparators[0], ast.Tuple) if test.comparators else False
        if left_tuple or right_tuple:
            # Every tuple element is a mock attribute or a literal expectation; any other
            # element is an output being checked. At least one mock attribute is needed.
            saw_mock = False
            for operand in (test.left, *test.comparators):
                if isinstance(operand, ast.Tuple):
                    for elt in operand.elts:
                        if isinstance(elt, ast.Constant):
                            continue
                        if not (isinstance(elt, ast.Attribute) and elt.attr in _MOCK_VERIFY_ATTRS):
                            # Found a non-mock element in a tuple comparison
                            return False
                        saw_mock = True
            return saw_mock
    return any(isinstance(n, ast.Attribute) and n.attr in _MOCK_VERIFY_ATTRS
               for n in ast.walk(test))


def _is_raises(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _call_name(node) in _RAISES_CALLS


def _has_hint(name: str) -> bool:
    return any(hint in name.lower() for hint in _ASSERTION_HINTS)


def _raises_block(node: ast.AST) -> bool:
    return isinstance(node, ast.With | ast.AsyncWith) and any(
        _is_raises(item.context_expr) for item in node.items
    )


def _asserting_helpers(tree: ast.Module, *, behavioral: bool = False) -> frozenset[str]:
    """Same-file functions that assert, directly or through each other (a fixed point).

    With *behavioral*, a function counts only if it has an assertion that is not
    shape-only (``isinstance``, ``is not None``, ...): a helper that only checks a
    type must not hide a ``type_only`` test.
    """
    defs: dict[str, list[_FuncDef]] = collections.defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, _FuncDef):
            defs[node.name].append(node)

    def direct(fn: _FuncDef) -> bool:
        return any(
            (isinstance(n, ast.Assert) and not (behavioral and _is_weak(n.test))) or _raises_block(n)
            or (isinstance(n, ast.Call) and _has_hint(_call_name(n)))
            for n in _own_nodes(fn.body)
        )

    def callees(fn: _FuncDef) -> set[str]:
        return {_call_name(n) for n in _own_nodes(fn.body) if isinstance(n, ast.Call)}

    asserting = {name for name, fns in defs.items() if any(map(direct, fns))}
    changed = True
    while changed:
        changed = False
        for name, fns in defs.items():
            if name not in asserting and any(callees(f) & asserting for f in fns):
                asserting.add(name)
                changed = True
    return frozenset(asserting)


class _Facts(NamedTuple):
    nodes: list[ast.AST]
    asserts: list[ast.Assert]
    value_asserts: list[ast.Assert]
    raises: bool
    calls: list[ast.Call]
    assert_calls: list[ast.Call]
    patching: bool


def _collect(fn: _FuncDef) -> _Facts:
    nodes = list(_own_nodes(fn.body))
    asserts = [n for n in nodes if isinstance(n, ast.Assert)]
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    # A @patch decorator patches the whole test from outside its body.
    decorator_calls = [n for d in fn.decorator_list for n in ast.walk(d) if isinstance(n, ast.Call)]
    return _Facts(
        nodes=nodes,
        asserts=asserts,
        value_asserts=[a for a in asserts if not _is_weak(a.test)],
        # Context-manager form, or callable form: pytest.raises(ValueError, int, "bad").
        raises=any(map(_raises_block, nodes)) or any(
            isinstance(n, ast.Call) and _call_name(n) in _RAISES_CALLS and len(n.args) >= 2
            for n in nodes),
        calls=calls,
        assert_calls=[c for c in calls if _call_name(c).startswith("assert")],
        patching=any(map(_is_patching_call, itertools.chain(calls, decorator_calls))),
    )


def _all_value_asserts(t: _Facts, pred: Callable[[ast.expr], bool]) -> bool:
    if not t.value_asserts or t.raises or t.assert_calls:
        return False
    return all(pred(a.test) for a in t.value_asserts)


def _shape_flags(fn: _FuncDef, names: frozenset[str], helpers: frozenset[str],
                 value_helpers: frozenset[str]) -> set[str]:
    t = _collect(fn)
    inputs = _constructor_inputs(t.nodes)
    checks = bool(t.asserts or t.raises or t.assert_calls)
    # A test that calls a helper with a behavioral assert has behavioral checks even if its
    # direct asserts are weak; a helper that only checks a shape does not count.
    helper_checks = any(_call_name(c) in value_helpers for c in t.calls)
    # For no_assert: bare calls with hint words count; same-file helpers count when called
    # bare or through self (a method of the test's own class). Other method calls
    # (e.g., v.validate) do not suppress no_assert: the receiver may be unrelated.
    has_hinted_bare_call = any(
        _has_hint(_call_name(c)) and isinstance(c.func, ast.Name) for c in t.calls
    )
    has_helper_call = any(
        _call_name(c) in helpers and (isinstance(c.func, ast.Name) or _is_self_call(c))
        for c in t.calls
    )
    flags = {
        "type_only": bool(t.asserts) and not (t.value_asserts or t.raises or t.assert_calls or helper_checks),
        "mock_echo": (
            t.patching and not t.raises and bool(t.asserts or t.assert_calls)
            and all(_is_mock_verify(a.test) for a in t.asserts)
            and all(_call_name(c) in _MOCK_VERIFY_METHODS for c in t.assert_calls)
        ),
        "set_then_read": _all_value_asserts(t, lambda e: _echoes_input(e, inputs)),
        "constant_mirror": _all_value_asserts(t, lambda e: _is_constant_mirror(e, names)),
        "no_assert": not checks and not (has_hinted_bare_call or has_helper_call),
    }
    found = {flag for flag, hit in flags.items() if hit}
    if found & set(_SHAPE_FLAGS) and _statement_count(fn.body) <= _TRIVIAL_MAX_STATEMENTS:
        found.add("trivial_size")
    return found


def _duplicates(tests: list[tuple[str, _FuncDef]]) -> set[str]:
    """Qualified names of tests that repeat an earlier test of the same class (or module)."""
    first: dict[tuple, str] = {}
    repeats: set[str] = set()
    for qualname, fn in tests:
        a = fn.args
        params = tuple(p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs))
        # Include parameter defaults in the identity
        defaults = tuple(ast.dump(d) for d in (a.defaults or []))
        kw_defaults = tuple(ast.dump(d) if d else "" for d in (a.kw_defaults or []))
        identity = (
            qualname.rpartition(".")[0],
            # Without line/column attributes two bodies at different lines dump the same.
            ast.dump(ast.Module(body=fn.body, type_ignores=[])),
            params, a.vararg and a.vararg.arg, a.kwarg and a.kwarg.arg,
            defaults, kw_defaults,
            tuple(ast.dump(d) for d in fn.decorator_list),
        )
        if identity in first:
            repeats.add(qualname)
        else:
            first[identity] = qualname
    return repeats


def _is_exempt(fn: _FuncDef, lines: list[str]) -> bool:
    """The marker, followed by a reason, on a decorator or signature line.

    A formatter may wrap the signature, so any line from the first decorator to
    the body counts. A bare marker exempts nothing.
    """
    start = min([d.lineno for d in fn.decorator_list] + [fn.lineno])
    end = max(fn.lineno, fn.body[0].lineno - 1)
    return any(
        lines[i - 1].partition(EXEMPT_MARKER)[2].strip()
        for i in range(start, end + 1) if EXEMPT_MARKER in lines[i - 1]
    )


def audit_source(source: str, file: str = "<source>") -> list[Audit]:
    """Audit every test function in *source*, flagged or not; ``SyntaxError`` if unparsable."""
    tree = ast.parse(source, filename=file)
    names = _package_names(tree)
    helpers = _asserting_helpers(tree)
    value_helpers = _asserting_helpers(tree, behavioral=True)
    lines = source.splitlines()
    tests = _test_functions(tree)
    duplicates = _duplicates(tests)
    audits = []
    for qualname, fn in tests:
        found = _shape_flags(fn, names, helpers, value_helpers)
        if qualname in duplicates:
            found.add("duplicate")
        flags = tuple(f for f in FLAGS if f in found)
        audits.append(Audit(file, fn.lineno, qualname, flags, _is_exempt(fn, lines)))
    return audits


def audit_tree(tests_dir: pathlib.Path = TESTS) -> tuple[list[Audit], int]:
    """``(flagged audits, test functions seen)`` for every ``test_*.py`` under *tests_dir*."""
    flagged: list[Audit] = []
    total = 0
    for path in sorted(tests_dir.rglob("test_*.py")):
        try:
            rel = path.resolve().relative_to(PROJECT).as_posix()
        except ValueError:
            rel = path.as_posix()
        results = audit_source(path.read_text(encoding="utf-8"), rel)
        total += len(results)
        flagged.extend(r for r in results if r.flags)
    return flagged, total


def gated_findings(audits: Iterable[Audit]) -> dict[str, set[str]]:
    """``flag -> {test key}`` for every gated flag, exempt tests left out."""
    found: dict[str, set[str]] = {flag: set() for flag in GATED_FLAGS}
    for audit in audits:
        if not audit.exempt:
            for flag in set(audit.flags) & set(GATED_FLAGS):
                found[flag].add(audit.key)
    return found


def ratchet_errors(found: dict[str, set[str]], baseline: dict[str, frozenset[str]]) -> list[str]:
    """Every way *found* breaks a baseline that only shrinks, one line each.

    A finding outside the baseline is new; a baselined key no longer found was
    fixed and must leave the baseline, or the finding could come back unnoticed.
    """
    errors: list[str] = []
    for flag in GATED_FLAGS:
        live, known = found.get(flag, set()), baseline.get(flag, frozenset())
        errors += [f"{flag}: new: {key}" for key in sorted(live - known)]
        errors += [f"{flag}: fixed, remove from the baseline: {key}" for key in sorted(known - live)]
    return errors


def report(flagged: list[Audit], total: int, top: int = 20) -> str:
    counts = collections.Counter(f for r in flagged for f in r.flags)
    per_file = collections.Counter(r.file for r in flagged)
    exempt = sum(r.exempt for r in flagged)
    lines = [f"test functions audited: {total}",
             f"flagged: {len(flagged)} ({exempt} exempt)", "", "flag counts:"]
    lines += [f"  {flag:<16} {counts[flag]:>5}" for flag in FLAGS]
    if per_file:
        lines += ["", "files with the most flagged tests:"]
        lines += [f"  {n:>4}  {file}" for file, n in per_file.most_common(top)]
    lines += [""] + [f"{r.key}  {','.join(r.flags)}{'  (exempt)' if r.exempt else ''}"
                     for r in flagged]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", type=pathlib.Path,
                        help="write every flagged test as {file, line, test, flags, exempt}")
    parser.add_argument("--tests-dir", type=pathlib.Path, default=TESTS,
                        help="directory to scan (default: tests/)")
    args = parser.parse_args(argv)

    flagged, total = audit_tree(args.tests_dir)
    print(report(flagged, total))
    if args.json:
        rows = [{**r._asdict(), "flags": list(r.flags)} for r in flagged]
        args.json.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {len(rows)} flagged tests to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
