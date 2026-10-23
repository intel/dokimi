# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""AST helpers of ``audit_test_quality.py``: names, own nodes, and which names in a test are mocks."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef

_PATCH_CALLS = frozenset(
    {"patch", "MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock"}
)

_MOCK_VERIFY_ATTRS = frozenset({"called", "call_count", "await_count", "awaited"})

_CALLED_FLAGS = frozenset({"called", "awaited"})

_CALL_COUNTS = frozenset({"call_count", "await_count"})

_PATCH_NEW_POSITION = {"patch": 1, "object": 2}

_MOCK_FACTORIES = frozenset({"MagicMock", "Mock", "AsyncMock", "NonCallableMock", "PropertyMock",
                             "create_autospec"})

def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""

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

def _stored_names(target: ast.expr) -> list[str]:
    """Every name a ``for`` or ``with`` target binds."""
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]

def _eq_operands(node: ast.expr) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
        return node.left, node.comparators[0]
    return None

def _is_patching_call(node: ast.Call) -> bool:
    dotted = _dotted(node.func)
    if dotted == "monkeypatch.setattr" or dotted.endswith(("patch.object", "patch.dict")):
        return True
    return _call_name(node) in _PATCH_CALLS

def _injected_patches(fn: _FuncDef) -> int:
    """How many ``@patch`` decorators hand the test a mock argument (no ``new`` given)."""
    count = 0
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        kind = "object" if _dotted(dec.func).endswith("patch.object") else _call_name(dec)
        position = _PATCH_NEW_POSITION.get(kind)
        if position is not None and len(dec.args) <= position and not any(
                kw.arg == "new" for kw in dec.keywords):
            count += 1
    return count

def _base(node: ast.expr) -> ast.expr:
    """The expression under ``x.a``, ``x["k"]`` and ``x()``; ``self.m`` stays whole."""
    while isinstance(node, ast.Attribute | ast.Subscript | ast.Call):
        inner = node.func if isinstance(node, ast.Call) else node.value
        if isinstance(node, ast.Attribute) and isinstance(inner, ast.Name) and inner.id == "self":
            return node
        node = inner
    return node

def _is_mock_source(value: ast.expr, mocks: set[str]) -> bool:
    """Whether *value* is a mock: built by a mock factory or a patch, or read off a mock."""
    while isinstance(value, ast.Attribute | ast.Subscript | ast.Call):
        if isinstance(value, ast.Call) and (
                _call_name(value) in _MOCK_FACTORIES or _is_patching_call(value)):
            return True
        if isinstance(value, ast.Attribute) and _dotted(value).startswith("self."):
            return _dotted(value).split(".")[1] in mocks
        value = value.func if isinstance(value, ast.Call) else value.value
    return isinstance(value, ast.Name) and value.id in mocks

def _propagate_mocks(nodes: list[ast.AST], mocks: set[str]) -> None:
    """Add the names assigned from a mock (``m = llm.return_value``), until nothing new is found."""
    assigns = [n for n in nodes if isinstance(n, ast.Assign | ast.AnnAssign) and n.value is not None]
    changed = True
    while changed:  # `m = p.start()` may come before or after `p = patch(...)` in walk order
        changed = False
        for node in assigns:
            assert node.value is not None
            if not _is_mock_source(node.value, mocks):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = (_dotted(target).split(".")[1] if _dotted(target).startswith("self.")
                        else target.id if isinstance(target, ast.Name) else None)
                if name is not None and name not in mocks:
                    mocks.add(name)
                    changed = True


def _mock_names(fn: _FuncDef, nodes: list[ast.AST]) -> frozenset[str]:
    """Names bound to a patch or a mock: ``@patch`` arguments, ``with patch(...) as m``,
    ``m = MagicMock()`` / ``mocker.patch(...)`` / ``p.start()``, the replacement handed to
    a patch (``monkeypatch.setattr(mod, "f", fake)``), and names read off those
    (``m = llm.return_value``). ``stats = run()`` binds data, not a mock."""
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    mocks = set(params[:_injected_patches(fn)])
    for node in nodes:
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if (item.optional_vars is not None and isinstance(item.context_expr, ast.Call)
                        and _is_patching_call(item.context_expr)):
                    mocks.update(_stored_names(item.optional_vars))
        elif (isinstance(node, ast.Call) and _is_patching_call(node)
              and _call_name(node) not in _MOCK_FACTORIES):
            replacements = [*node.args[1:], *(kw.value for kw in node.keywords)]
            mocks.update(r.id for r in replacements if isinstance(r, ast.Name))
    _propagate_mocks(nodes, mocks)
    return frozenset(mocks)

def _on_mock(attr: ast.Attribute, mocks: frozenset[str]) -> bool:
    """Whether ``x.called`` is read off a mock (a fixture named ``mock_*`` counts)."""
    base = _base(attr.value)
    name = (base.attr if isinstance(base, ast.Attribute)
            else base.id if isinstance(base, ast.Name) else "")
    return name in mocks or "mock" in name.lower()

def _mock_read(node: ast.AST, mocks: frozenset[str]) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in _MOCK_VERIFY_ATTRS
            and _on_mock(node, mocks))

def _is_absence(test: ast.expr, mocks: frozenset[str]) -> bool:
    """``not m.called`` or ``m.call_count == 0``: the code under test chose not to call."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return (isinstance(test.operand, ast.Attribute) and test.operand.attr in _CALLED_FLAGS
                and _on_mock(test.operand, mocks))
    operands = _eq_operands(test)
    return operands is not None and any(
        isinstance(a, ast.Attribute) and a.attr in _CALL_COUNTS and _on_mock(a, mocks)
        and isinstance(b, ast.Constant) and b.value == 0
        for a, b in (operands, operands[::-1])
    )

def _tuple_compare_verdict(test: ast.expr, mocks: frozenset[str]) -> bool | None:
    """For ``(a, b) == (x, y)``: whether every tuple element is a mock read or a literal (and one is a
    mock read). None when the test is not an equality with a tuple side."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return None
    sides = (test.left, *test.comparators)
    if not any(isinstance(side, ast.Tuple) for side in sides):
        return None
    # Every tuple element is a mock attribute or a literal expectation; any other
    # element is an output being checked. At least one mock attribute is needed.
    elements = [e for side in sides if isinstance(side, ast.Tuple) for e in side.elts
                if not isinstance(e, ast.Constant)]
    return bool(elements) and all(_mock_read(e, mocks) for e in elements)


def _is_mock_verify(test: ast.expr, mocks: frozenset[str]) -> bool:
    """An assert that only reads whether a mock was called.

    ``assert m.call_count == 2 and vote.aggregate == 70.0`` also checks a value,
    so each operand of an ``and`` is judged on its own. An ``or`` passes as soon as
    one operand does, so one mock-only operand makes it mock-only.

    Tuple comparisons like ``assert (m.call_count, out) == (1, 42)`` check both
    the mock call and the output, so they are not mock-only. But
    ``assert (m.call_count, m.called) == (1, True)`` is mock-only.

    Only reads off *mocks* count: ``stats.call_count`` on a value the code under test
    returned is data, and ``not m.called`` / ``m.call_count == 0`` is an absence.
    """
    if isinstance(test, ast.BoolOp):
        if isinstance(test.op, ast.And):
            return all(_is_mock_verify(v, mocks) for v in test.values)
        elif isinstance(test.op, ast.Or):
            return any(_is_mock_verify(v, mocks) for v in test.values)
    if _is_absence(test, mocks):
        return False
    tuple_verdict = _tuple_compare_verdict(test, mocks)
    if tuple_verdict is not None:
        return tuple_verdict
    return any(_mock_read(n, mocks) for n in ast.walk(test))

def _add_held_by_class(tree: ast.Module, found: dict[int, frozenset[str]]) -> None:
    """Add to each test the mocks the non-test methods of its class (``setUp``) hold."""
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        held: set[str] = set()
        for m in cls.body:
            if isinstance(m, _FuncDef) and not m.name.startswith("test_"):
                held |= _mock_names(m, list(_own_nodes(m.body)))
        for m in cls.body:
            if isinstance(m, _FuncDef) and m.name.startswith("test_"):
                found[id(m)] = found.get(id(m), frozenset()) | held


def _outer_mocks(tree: ast.Module) -> dict[int, frozenset[str]]:
    """``id(test) -> names`` of mocks the test gets from outside its body.

    ``self.m = MagicMock()`` in a ``setUp`` (or any other non-test method of the class), and a
    parameter named like a fixture of the same file whose body builds a mock or a patch. A
    fixture of a ``conftest.py`` is not seen: its mock is tracked only by a ``mock`` in its name.
    """
    def builds_mock(fn: _FuncDef) -> bool:
        return any(isinstance(n, ast.Call) and (_call_name(n) in _MOCK_FACTORIES or _is_patching_call(n))
                   for n in _own_nodes(fn.body))

    def is_fixture(fn: _FuncDef) -> bool:
        return any("fixture" in _dotted(d.func if isinstance(d, ast.Call) else d) for d in fn.decorator_list)

    fixtures = {f.name for f in ast.walk(tree)
                if isinstance(f, _FuncDef) and is_fixture(f) and builds_mock(f)}
    found: dict[int, frozenset[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, _FuncDef) and fn.name.startswith("test_"):
            a = fn.args
            found[id(fn)] = frozenset(p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)) & fixtures
    _add_held_by_class(tree, found)
    return found
