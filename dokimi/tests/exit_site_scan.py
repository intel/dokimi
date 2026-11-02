# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which lines of one module end the process: the AST scan behind the exit-site rule.

Used by tests/test_import_architecture.py and tests/test_exit_sites.py. No import is run.
"""

from __future__ import annotations

import ast


#: The exit helper: its own raise is not counted, a call to it is (like SPAWN_WRAPPER).
EXIT_HELPER = "dokimi.exit_codes"

_EXIT_CALLS = {("sys", "exit"), ("os", "_exit"), ("builtins", "exit"), ("builtins", "quit"),
               (EXIT_HELPER, "usage_exit")}
_EXIT_MODULES = {m for m, _ in _EXIT_CALLS}


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, *_COMPREHENSIONS)
_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _header_roots(node: ast.AST) -> list[ast.AST]:
    """The parts of a def, class or comprehension that Python evaluates in the enclosing scope."""
    if isinstance(node, ast.ClassDef):
        return [*node.bases, *node.keywords, *node.decorator_list]
    if isinstance(node, _COMPREHENSIONS):
        return [node.generators[0].iter]  # only the outermost iterable
    args = node.args
    named = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    roots = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
    roots += [a.annotation for a in named if a is not None and a.annotation is not None]
    if not isinstance(node, ast.Lambda):
        roots += [*node.decorator_list, *([node.returns] if node.returns is not None else [])]
    return roots


def _own_nodes(root: ast.AST) -> list[ast.AST]:
    """``root`` and its descendants up to a nested scope, which is listed but not entered."""
    found, stack = [], [root]
    while stack:
        node = stack.pop()
        found.append(node)
        if not isinstance(node, _SCOPE_NODES):
            stack.extend(ast.iter_child_nodes(node))
    return found


def _scope_owners(tree: ast.Module) -> dict[int, ast.AST]:
    """``id(node) -> `` the function, lambda, class, comprehension or module whose names ``node`` binds or uses."""
    owner: dict[int, ast.AST] = {}
    stack: list[tuple[ast.AST, ast.AST]] = [(tree, tree)]
    while stack:
        node, scope = stack.pop()
        for child in ast.iter_child_nodes(node):
            owner[id(child)] = scope
            stack.append((child, child if isinstance(child, _SCOPE_NODES) else scope))
    for scope in ast.walk(tree):  # outer scopes first, so a header is moved from its final owner
        if isinstance(scope, _SCOPE_NODES):
            for root in _header_roots(scope):
                for node in _own_nodes(root):
                    owner[id(node)] = owner[id(scope)]
    return owner


#: ``(id of the scope, name) -> [(pos, kind, payload)]``; ``pos`` is where the binding takes effect.
#: The kinds and their payloads are described in the docstring of ``_bind``.
ExitTable = dict[tuple[int, str], list[tuple[tuple[int, int], str, object]]]
#: A binding moved to another scope runs when its function is called, after any statement of the module.
_LATER = (10**9, 0)


def _at(node: ast.AST) -> tuple[int, int]:
    return (node.lineno, node.col_offset)


def _after(node: ast.AST) -> tuple[int, int]:
    """Where the bindings of a statement or a name take effect: after its value is evaluated."""
    return (node.end_lineno, node.end_col_offset)


def _bind(table: ExitTable, scope: ast.AST, name: str, pos: tuple[int, int], kind: str, payload: object = None) -> None:
    """Record a binding of ``name`` in ``scope`` at ``pos``.

    Kinds: ``mod`` (payload: modules), ``fn`` (payload: ``(module, attr)`` of an exit callable), ``sysexit``
    (SystemExit, a name for it, or an instance), ``other`` (any other binding, shadowing outer names), and
    ``global`` / ``nonlocal`` (payload: the scope the name is declared in, where it is bound).
    """
    table.setdefault((id(scope), name), []).append((pos, kind, payload))


def _import_bindings(node: ast.Import | ast.ImportFrom, scope: ast.AST, table: ExitTable) -> None:
    pos = _after(node)
    if isinstance(node, ast.Import):  # ``import sys as s`` binds s; ``import dokimi.exit_codes`` binds that dotted name
        for a in node.names:
            _bind(table, scope, a.asname or a.name, pos, "mod", frozenset({a.name}))
        return
    # ``from .exit_codes import x`` and ``from . import exit_codes`` name dokimi.exit_codes
    module = node.module
    if node.level:
        module = {"exit_codes": EXIT_HELPER, None: "dokimi"}.get(node.module)
    for a in node.names:
        name = a.asname or a.name
        if (module, a.name) in _EXIT_CALLS:  # ``from sys import exit as bye``
            _bind(table, scope, name, pos, "fn", (module, a.name))
        elif f"{module}.{a.name}" in _EXIT_MODULES:  # ``from dokimi import exit_codes as ec``
            _bind(table, scope, name, pos, "mod", frozenset({f"{module}.{a.name}"}))
        elif module == "builtins" and a.name == "SystemExit" and not node.level:  # ``from builtins import SystemExit as Stop``
            _bind(table, scope, name, pos, "sysexit")
        else:
            _bind(table, scope, name, pos, "other")


def _params(args: ast.arguments) -> list[str]:
    named = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    return [a.arg for a in named if a is not None]


def _late_targets(tree: ast.Module) -> dict[int, tuple[int, int]]:
    """``id(name) -> pos`` for the names a for, with or augmented assignment binds: after the iterable, context or value."""
    late: dict[int, tuple[int, int]] = {}

    def mark(target: ast.AST, pos: tuple[int, int]) -> None:
        late.update((id(m), pos) for m in ast.walk(target) if isinstance(m, ast.Name))

    for n in ast.walk(tree):
        if isinstance(n, (ast.For, ast.AsyncFor)):
            mark(n.target, _after(n.iter))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if item.optional_vars is not None:
                    mark(item.optional_vars, _after(item.context_expr))
        elif isinstance(n, ast.AugAssign):
            mark(n.target, _after(n.value))
    return late


def _capture_bindings(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable) -> None:
    """``case x:``, ``case [*x]:`` and ``case {**x}:`` bind x once the pattern has matched."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.MatchAs, ast.MatchStar, ast.MatchMapping)):
            name = n.rest if isinstance(n, ast.MatchMapping) else n.name
            if name:
                _bind(table, owner[id(n)], name, _after(n), "other")


def _shadowing_bindings(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable, assigned: set[int]) -> None:
    """Every binding that is not a plain assignment of one name: imports, defs, parameters, loop, with and except targets."""
    bare = {id(n.target) for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and n.value is None}
    late = _late_targets(tree)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            _import_bindings(n, owner[id(n)], table)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # the name binds after the header (decorators, defaults, bases), which the body follows
            _bind(table, owner[id(n)], n.name, _at(n.body[0]), "other")
        elif isinstance(n, ast.ExceptHandler) and n.name:
            _bind(table, owner[id(n)], n.name, _at(n.body[0]), "other")
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and id(n) not in assigned:
            # ``x: int`` binds nothing at run time outside a function, where it makes x local
            if id(n) not in bare or isinstance(owner[id(n)], _FUNC_NODES):
                _bind(table, owner[id(n)], n.id, late.get(id(n), _after(n)), "other")
        if isinstance(n, _FUNC_NODES):  # a parameter is local to its function, bound on entry
            for name in _params(n.args):
                _bind(table, n, name, _at(n), "other")


def _target_scope(scope: ast.AST, name: str, table: ExitTable) -> ast.AST:
    """The scope an assignment to ``name`` in ``scope`` binds: where ``global`` or ``nonlocal`` declared it, else ``scope``."""
    for b in table.get((id(scope), name), []):
        if b[1] in ("global", "nonlocal"):
            return b[2]
    return scope


def _value_binding(expr: ast.expr, scope: ast.AST, pos: tuple[int, int], table: ExitTable, owner: dict[int, ast.AST]):
    """The (kind, payload) that ``x = expr`` gives x: SystemExit, an exit callable, a module, or other."""
    if _is_sysexit_expr(expr, scope, pos, table, owner):
        return "sysexit", None
    if isinstance(expr, ast.Name):  # ``stop = exit_alias`` copies the binding it names
        b = _resolve(expr.id, scope, pos, table, owner)
        if b is None and expr.id in ("exit", "quit"):  # the builtins, where nothing binds the name
            return "fn", ("builtins", expr.id)
        if b is not None and b[1] in ("fn", "mod"):
            return b[1], b[2]
    if isinstance(expr, (ast.Name, ast.Attribute)):  # ``s = sys`` or ``ec = dokimi.exit_codes``
        mods = _modules(ast.unparse(expr), scope, pos, table, owner)
        if mods:
            return "mod", frozenset(mods)
    if isinstance(expr, ast.Attribute):  # ``stop = sys.exit``
        for m in _modules(ast.unparse(expr.value), scope, pos, table, owner):
            if (m, expr.attr) in _EXIT_CALLS:
                return "fn", (m, expr.attr)
    return "other", None


def _pairs(target: ast.expr, value: ast.expr) -> list[tuple[ast.Name, ast.expr]]:
    """The (name, value) pairs of ``target = value`` where both unpack literally: ``a, (b, c) = x, (y, z)``.

    A target that does not unpack against a literal of the same shape gives no pairs; its names bind as other names.
    """
    if isinstance(target, ast.Name):
        return [(target, value)]
    if not (isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List))):
        return []
    if len(target.elts) != len(value.elts) or any(isinstance(e, ast.Starred) for e in [*target.elts, *value.elts]):
        return []
    return [pair for t, v in zip(target.elts, value.elts, strict=True) for pair in _pairs(t, v)]


def _assigned(assigns: list[tuple[ast.AST, list[ast.expr]]], owner: dict[int, ast.AST], table: ExitTable):
    """The ``(key, entry)`` each assignment makes against ``table``, in source order."""
    out = []
    for n, targets in sorted(assigns, key=lambda p: _after(p[0])):
        scope = owner[id(n)]
        if isinstance(n, ast.NamedExpr):  # a walrus binds in the nearest enclosing def, lambda, class or module
            while isinstance(scope, _COMPREHENSIONS):
                scope = owner[id(scope)]
        for t in targets:
            for name, expr in _pairs(t, n.value):
                kind, payload = _value_binding(expr, owner[id(n)], _at(expr), table, owner)
                target = _target_scope(scope, name.id, table)
                pos = _after(n) if target is scope else _LATER
                out.append(((id(target), name.id), (pos, kind, payload)))
    return out


def _assignment_bindings(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable) -> None:
    """``x = value`` and ``(x := value)`` bind x to what the value is, once the value is evaluated.

    A value can name a binding that another assignment makes later in the source (a function may run after it),
    so the entries are recomputed until they settle: each pass reads the entries of the pass before it, then replaces them.
    """
    assigns: list[tuple[ast.AST, list[ast.expr]]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            assigns.append((n, n.targets))
        elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None:
            assigns.append((n, [n.target]))
    entries: list[tuple[tuple[int, str], tuple]] = []
    for _ in range(len(assigns) + 1):
        fresh = _assigned(assigns, owner, table)
        for key, entry in entries:
            table[key].remove(entry)
        for key, entry in fresh:
            table.setdefault(key, []).append(entry)
        if fresh == entries:
            break
        entries = fresh


def _declared_scopes(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable, kind: str) -> None:
    """``global x`` declares x in the module and ``nonlocal x`` in the nearest enclosing scope that binds it.

    The bindings made in the declaring scope move to that scope, where they run when the function is called. The
    declaration stays in the scope, so that its own uses resolve there. ``kind`` is ``global`` or ``nonlocal``: a
    nonlocal binder can be an assignment, so those declarations come after the assignments are bound.
    """
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Global, ast.Nonlocal)) or (kind == "global") != isinstance(n, ast.Global):
            continue
        scope = owner[id(n)]
        for name in n.names:
            target = tree if kind == "global" else _binder_above(scope, name, table, owner)
            if target is None or target is scope:
                continue
            # the declarations already in the scope stay there; only the bindings made in it move to the target
            held = table.pop((id(scope), name), [])
            table.setdefault((id(target), name), []).extend(
                (_LATER, k, p) for _, k, p in held if k not in ("global", "nonlocal"))
            table.setdefault((id(scope), name), []).extend(
                b for b in held if b[1] in ("global", "nonlocal"))
            table[(id(scope), name)].append((_at(n), kind, target))


def _binder_above(scope: ast.AST, name: str, table: ExitTable, owner: dict[int, ast.AST]) -> ast.AST | None:
    outer = owner.get(id(scope))
    while outer is not None:
        # a scope that only declares the name (global or nonlocal) binds nothing: its bindings went elsewhere
        if any(b[1] not in ("global", "nonlocal") for b in table.get((id(outer), name), [])):
            return outer
        outer = owner.get(id(outer))
    return None


def _exit_table(tree: ast.Module, owner: dict[int, ast.AST]) -> ExitTable:
    # the targets of an assignment with a value; ``x: int`` alone binds x (local to its function) as any other name
    assigned: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            targets = n.targets
        elif isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None:
            targets = [n.target]
        else:
            continue
        value = n.value
        assigned |= {id(name) for t in targets for name, _ in _pairs(t, value)}
    table: ExitTable = {}
    _shadowing_bindings(tree, owner, table, assigned)
    _capture_bindings(tree, owner, table)
    _declared_scopes(tree, owner, table, "global")
    _assignment_bindings(tree, owner, table)
    _declared_scopes(tree, owner, table, "nonlocal")
    return table


def _resolve(name: str, use: ast.AST, pos: tuple[int, int], table: ExitTable, owner: dict[int, ast.AST]):
    """The binding that ``name`` denotes at ``pos`` in scope ``use``, or None when no scope binds it.

    Python's scoping: the innermost scope that binds the name wins, unless it declares the name ``global`` or
    ``nonlocal``, which sends the lookup to the scope that binds it. In the use's own scope the last binding at
    or before ``pos`` decides, and a binding only after it leaves the name local and unbound there; a class body
    then reads the enclosing names. An enclosing scope is read as its last exit-kind binding: a function may run
    after any rebinding. A class body is seen only from its own statements, not from the functions inside it.
    A module-level name read before its first binding is read as the builtin of that name, since it is then unbound.
    """
    scope, here = use, True
    while True:
        if here or not isinstance(scope, ast.ClassDef):
            found = table.get((id(scope), name), [])
            declared = [b for b in found if b[1] in ("global", "nonlocal")]
            if declared and declared[-1][2] is not scope:
                scope, here = declared[-1][2], False
                continue
            if found and not here:  # a function may run later: its exit-kind binding counts
                exits = [b for b in found if b[1] != "other"] or found
                return sorted(exits, key=lambda b: b[0])[-1]
            if found:
                before = [b for b in found if b[0] <= pos]
                if before:
                    return sorted(before, key=lambda b: b[0])[-1]
                if isinstance(use, _FUNC_NODES + _COMPREHENSIONS):  # a function's local, not bound yet
                    return (pos, "other", None)
        if id(scope) not in owner:  # the module: no enclosing scope
            return None
        scope, here = owner[id(scope)], False


def _modules(text: str, scope: ast.AST, pos: tuple[int, int], table: ExitTable, owner: dict[int, ast.AST]) -> set[str]:
    """The modules a dotted name stands for at this use: an import binding, or sys and os when nothing binds them."""
    b = _resolve(text, scope, pos, table, owner)
    if b is None:
        return {text} if text in ("sys", "os") else set()
    return set(b[2]) if b[1] == "mod" else set()


def _is_sysexit_expr(expr: ast.expr, scope: ast.AST, pos: tuple[int, int], table: ExitTable, owner: dict[int, ast.AST]) -> bool:
    """Whether ``expr`` is SystemExit, a name bound to it, or an instance of it (a call of either)."""
    if isinstance(expr, ast.Call):
        return _is_sysexit_expr(expr.func, scope, pos, table, owner)
    if isinstance(expr, ast.Name):
        b = _resolve(expr.id, scope, pos, table, owner)
        return expr.id == "SystemExit" if b is None else b[1] == "sysexit"
    if isinstance(expr, ast.Attribute):  # builtins.SystemExit, with ``import builtins``
        return expr.attr == "SystemExit" and "builtins" in _modules(ast.unparse(expr.value), scope, pos, table, owner)
    return False


def _calls_exit(node: ast.Call, scope: ast.AST, table: ExitTable, owner: dict[int, ast.AST]) -> bool:
    func = node.func
    pos = _at(node)
    if isinstance(func, ast.Name):  # exit(), quit(), or a name bound to sys.exit and the like
        b = _resolve(func.id, scope, pos, table, owner)
        return func.id in ("exit", "quit") if b is None else b[1] == "fn" and b[2] in _EXIT_CALLS
    return isinstance(func, ast.Attribute) and any(
        (m, func.attr) in _EXIT_CALLS for m in _modules(ast.unparse(func.value), scope, pos, table, owner))


def exit_sites(tree: ast.Module) -> list[int]:
    """Lines that end the process: an exit call (by module attribute, through an alias, or builtin name) or a raise of SystemExit.

    The ``if __name__ == '__main__':`` block of a script is the process boundary of ``python -m``, so it is
    not counted. Each name resolves by scope and by source position (see ``_resolve``): an import, a parameter or
    an assignment in one function does not change what another function's name means, a method does not see the
    names of its class body, and a binding that follows a use on the same line does not count for it. Linear, not
    control-flow aware: ``if c: e = SystemExit(2)`` then ``else: e = ValueError()`` is read as the else binding.

    Known gap, where a real exit is missed: a module-level import followed by a later rebinding such as
    ``except ImportError: sys = None`` is read as the rebinding by the code after it.
    """
    guarded = {id(n) for stmt in tree.body
               if isinstance(stmt, ast.If) and ast.unparse(stmt.test) == "__name__ == '__main__'"
               for body_stmt in stmt.body for n in ast.walk(body_stmt)}
    owner = _scope_owners(tree)
    table = _exit_table(tree, owner)
    lines: list[int] = []
    raised: set[int] = set()  # the call of a raise is counted once, by the raise
    for node in ast.walk(tree):
        if id(node) in guarded or id(node) in raised:
            continue
        if isinstance(node, ast.Raise) and node.exc is not None:
            if _is_sysexit_expr(node.exc, owner[id(node)], _at(node.exc), table, owner):
                lines.append(node.lineno)
                raised.add(id(node.exc))
        elif isinstance(node, ast.Call) and _calls_exit(node, owner[id(node)], table, owner):
            lines.append(node.lineno)
    return sorted(lines)
