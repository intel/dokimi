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

def _own_nodes(body: Iterable[ast.stmt], skip: tuple[type, ...] = _SCOPES) -> Iterator[ast.AST]:
    """Every node of a body, not descending into nested functions or classes (or *skip*)."""
    stack: list[ast.AST] = [n for n in body if not isinstance(n, skip)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, skip))

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

def _self_attr(node: ast.expr) -> str:
    """``self.m`` for ``self.m`` or ``self.m.x``: a mock held on self is never a bare ``m``."""
    return "self." + _dotted(node).split(".")[1]

def _is_mock_source(value: ast.expr, mocks: set[str]) -> bool:
    """Whether *value* is a mock: built by a mock factory or a patch, or read off a mock."""
    while isinstance(value, ast.Attribute | ast.Subscript | ast.Call):
        if isinstance(value, ast.Call) and (
                _call_name(value) in _MOCK_FACTORIES or _is_patching_call(value)):
            return True
        if isinstance(value, ast.Attribute) and _dotted(value).startswith("self."):
            return _self_attr(value) in mocks
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
                name = (_self_attr(target) if _dotted(target).startswith("self.")
                        else target.id if isinstance(target, ast.Name) else None)
                if name is not None and name not in mocks:
                    mocks.add(name)
                    changed = True


def _mock_names(fn: _FuncDef, nodes: list[ast.AST], seed: frozenset[str] = frozenset()) -> frozenset[str]:
    """Names bound to a patch or a mock: ``@patch`` arguments, ``with patch(...) as m``,
    ``m = MagicMock()`` / ``mocker.patch(...)`` / ``p.start()``, the replacement handed to
    a patch (``monkeypatch.setattr(mod, "f", fake)``), and names read off those
    (``m = llm.return_value``). ``stats = run()`` binds data, not a mock. *seed* holds the
    mocks the test gets from outside its body, so ``m = self.m`` is read off one too."""
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    mocks = set(params[:_injected_patches(fn)]) | seed
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
    name = (f"self.{base.attr}" if isinstance(base, ast.Attribute)
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

def _scope_members(scope: ast.AST) -> Iterator[ast.AST]:
    """The functions and classes a module or class defines, also those under ``if``, ``try``,
    ``with``, a loop or ``match``: they are in the same scope."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            yield child
        elif isinstance(child, ast.stmt | ast.excepthandler | ast.match_case):
            yield from _scope_members(child)


def _add_held_by_class(tree: ast.Module, found: dict[int, frozenset[str]]) -> None:
    """Add to each test the mocks the non-test methods of its class (``setUp``) hold on self.

    Only ``self.m`` reaches the test: a local ``m`` of ``setUp`` is gone when the test runs."""
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        methods = [m for m in _scope_members(cls) if isinstance(m, _FuncDef)]
        held: set[str] = set()
        for m in methods:
            if not m.name.startswith("test_"):
                held |= {n for n in _mock_names(m, list(_own_nodes(m.body))) if n.startswith("self.")}
        for m in methods:
            if m.name.startswith("test_"):
                found[id(m)] = found.get(id(m), frozenset()) | held


def _is_fixture(fn: _FuncDef) -> bool:
    return any("fixture" in _dotted(d.func if isinstance(d, ast.Call) else d) for d in fn.decorator_list)


_WRAPPERS = frozenset({"SimpleNamespace", "dict"})


def _holds_mock(value: ast.expr, mocks: set[str]) -> bool:
    """A mock or a patch, or a container with one among its own items: a tuple, list, set or
    dict display, or ``SimpleNamespace(...)`` / ``dict(...)``. Another call is data, even when a
    mock is among its arguments (``run(m)``): it may return anything."""
    if isinstance(value, ast.Tuple | ast.List | ast.Set):
        items: list[ast.expr] = value.elts
    elif isinstance(value, ast.Dict):
        items = value.values
    elif isinstance(value, ast.Call) and _call_name(value) in _WRAPPERS:
        items = [*value.args, *(k.value for k in value.keywords)]
    else:
        return _is_mock_source(value, mocks)
    return any(_is_mock_source(i, mocks) for i in items)


def _returns_mock(fn: _FuncDef) -> bool:
    """Whether a fixture hands its test a mock or a patch, by ``return`` or ``yield``, alone or in a
    tuple, list, set or dict (``return {"llm": MagicMock()}``).

    A fixture that builds a mock to make its data (``return run(MagicMock())``) hands data."""
    nodes = list(_own_nodes(fn.body))
    mocks = set(_mock_names(fn, nodes))
    return any(isinstance(n, ast.Return | ast.Yield | ast.YieldFrom) and n.value is not None
               and _holds_mock(n.value, mocks) for n in nodes)


def _parametrize_arg(dec: ast.Call, position: int, keyword: str) -> ast.expr | None:
    return dec.args[position] if len(dec.args) > position else next(
        (k.value for k in dec.keywords if k.arg == keyword), None)


def _strings(node: ast.expr | None) -> set[str]:
    """The strings of a constant or of a list or tuple of them, each split at commas."""
    items = node.elts if isinstance(node, ast.List | ast.Tuple) else [node]
    return {p.strip() for i in items if isinstance(i, ast.Constant) and isinstance(i.value, str)
            for p in i.value.split(",")}


def _parametrized(node: _FuncDef | ast.ClassDef) -> set[str]:
    """The names a ``parametrize`` decorator fills: such a parameter is not a fixture's value.

    A name ``indirect=`` hands to its fixture is (``True`` hands them all, a list the ones it names)."""
    names: set[str] = set()
    for dec in node.decorator_list:
        if not (isinstance(dec, ast.Call) and _dotted(dec.func).endswith("parametrize")):
            continue
        given = _strings(_parametrize_arg(dec, 0, "argnames"))
        indirect = _parametrize_arg(dec, 2, "indirect")
        if isinstance(indirect, ast.Constant) and indirect.value:
            continue
        names |= given - _strings(indirect)
    return names


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _rebound(fn: _FuncDef) -> set[str]:
    """Names the test assigns and then reads: after ``stats = run()`` the parameter's value is gone.

    A comprehension's variable is its own, a store after the last read leaves the checked value,
    and a store of a mock read off the same name (``llm = llm.return_value``) keeps it a mock."""
    nodes = list(_own_nodes(fn.body, _SCOPES + _COMPREHENSIONS))
    kept = {id(t) for a in nodes if isinstance(a, ast.Assign | ast.AnnAssign) and a.value is not None
            for t in (a.targets if isinstance(a, ast.Assign) else [a.target])
            if isinstance(t, ast.Name) and _is_mock_source(a.value, {t.id})}
    first_store: dict[str, tuple[int, int]] = {}
    last_load: dict[str, tuple[int, int]] = {}
    for n in nodes:
        if isinstance(n, ast.Name) and id(n) not in kept:
            at = (n.lineno, n.col_offset)
            if isinstance(n.ctx, ast.Store):
                first_store[n.id] = min(first_store.get(n.id, at), at)
            elif isinstance(n.ctx, ast.Load):
                last_load[n.id] = max(last_load.get(n.id, at), at)
    return {name for name, at in first_store.items() if at < last_load.get(name, (0, 0))}


def _add_fixture_params(scope: ast.Module | ast.ClassDef, visible: dict[str, bool],
                        found: dict[int, frozenset[str]], given: frozenset[str] = frozenset()) -> None:
    """Map each test in *scope* to its parameters that a fixture in scope fills with a mock.

    A fixture of a class is seen by the tests of that class and of the classes in it, one at
    module level by every test, and the nearest fixture of a name wins. *visible* maps a fixture
    name to whether it returns a mock; *given* holds the names a class's ``parametrize`` fills."""
    members = list(_scope_members(scope))
    visible = {**visible, **{f.name: _returns_mock(f) for f in members if isinstance(f, _FuncDef) and _is_fixture(f)}}
    for node in members:
        if isinstance(node, ast.ClassDef):
            _add_fixture_params(node, visible, found, given | _parametrized(node))
        elif isinstance(node, _FuncDef) and node.name.startswith("test_"):
            a = node.args
            params = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
            params -= given | _parametrized(node) | _rebound(node)
            found[id(node)] = frozenset(p for p in params if visible.get(p))


def _outer_mocks(tree: ast.Module) -> dict[int, frozenset[str]]:
    """``id(test) -> names`` of mocks the test gets from outside its body.

    ``self.m = MagicMock()`` in a ``setUp`` (or any other non-test method of the class), held as
    ``self.m``, and a parameter filled by a fixture of the same file in the test's scope that
    returns or yields a mock or a patch. A fixture of a ``conftest.py`` is not seen: its mock is
    tracked only by a ``mock`` in its name.
    """
    found: dict[int, frozenset[str]] = {}
    _add_fixture_params(tree, {}, found)
    _add_held_by_class(tree, found)
    return found
