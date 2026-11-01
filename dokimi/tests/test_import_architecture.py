# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The package's import contract: layers, no new import cycles, wrapped clients.

Three rules, read from one import graph of ``dokimi/`` (``ast``, no import is run):

* **Layers.** Every module sits in one layer of :data:`LAYERS` and imports only from
  its own layer or the layers below it. ``cli`` and ``commands`` are the front end; no
  other module imports them. An edge already breaking this is in
  :data:`LAYER_BASELINE` with its reason.
* **Cycles.** An import cycle is a strongly connected component (SCC) of the graph.
  Every SCC must be one of :data:`CYCLE_BASELINE`: no new cycle, no module joining a
  baselined one. A baselined cycle that shrinks or breaks leaves the baseline in the
  same change, so it cannot grow back.
* **Wrapped clients.** LLM provider SDKs are imported only by :mod:`dokimi.llm`, and a
  child process is started only by :mod:`dokimi.process`. Exceptions are in
  :data:`BANNED_BASELINE` and :data:`SPAWN_BASELINE`.

Function-level (lazy) imports count: they defer a cycle to call time, they do not
remove the dependency. Imports under ``if TYPE_CHECKING:`` never run, so they count
for layers but not for cycles.

A baseline only shrinks. Never add an entry to make a new import pass: import from
the right layer, or move the shared piece down. Each rule has a test below that plants
a violation in a scratch package and checks the rule fails on it, so a broken checker
cannot pass silently.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import pytest

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = "dokimi"

#: Layers, top first. An entry names a module or a package (and everything under it);
#: the bare package name means ``dokimi/__init__.py`` only. A module may import from
#: its own layer and any layer below it.
LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The argparse front end: parses flags, calls into the tracks, prints.
    ("frontend", ("dokimi.__main__", "dokimi.cli", "dokimi.cli_routes", "dokimi.commands")),
    # Whole tracks and the tools beside them: static run, novelty, executable tasks,
    # repositories, subjects, reports, dashboards, the one-command pipeline.
    ("tracks", (
        "dokimi.config_check", "dokimi.context", "dokimi.coverage", "dokimi.dashboard",
        "dokimi.datasets_export", "dokimi.explore", "dokimi.external", "dokimi.harnesses",
        "dokimi.novelty", "dokimi.orchestrator", "dokimi.report", "dokimi.repos",
        "dokimi.runner", "dokimi.runs", "dokimi.schema_check", "dokimi.schema_compat",
        "dokimi.subjects", "dokimi.task_formats", "dokimi.tool_preference",
    )),
    # The LLM evaluation engine: answering, judging, panels, arms, questions, personas.
    ("evaluation", (
        "dokimi.eval", "dokimi.personas", "dokimi.plugins", "dokimi.questions",
        "dokimi.treatments",
    )),
    # Content loaders and pure scoring: doc sources, skills, agent profiles, Markdown
    # ingest, static metrics, gates.
    ("components", (
        "dokimi.agent_profiles", "dokimi.gate", "dokimi.ingest", "dokimi.mcp",
        "dokimi.metrics", "dokimi.skills",
    )),
    # What everything shares: defaults, registries, artifact and record I/O, the LLM and
    # process chokepoints, console output.
    ("foundation", (
        "dokimi", "dokimi._version", "dokimi.artifacts", "dokimi.caching", "dokimi.cli_flags", "dokimi.exit_codes",
        "dokimi.config", "dokimi.console", "dokimi.container_policy", "dokimi.defaults",
        "dokimi.intents", "dokimi.judged", "dokimi.llm", "dokimi.llm_cli", "dokimi.llm_json",
        "dokimi.llm_pricing", "dokimi.measure", "dokimi.process", "dokimi.process_io",
        "dokimi.records", "dokimi.registry", "dokimi.utils",
        # Token/cost record the LLM chokepoint fills; it lives under metrics/ but
        # depends on nothing and is read by every LLM caller.
        "dokimi.metrics.usage",
    )),
)

#: Imports that already cross a layer upward, (importer, imported): reason.
#: Empty: the novelty gate reads coverage from dokimi.metrics.bundle_coverage.
LAYER_BASELINE: dict[tuple[str, str], str] = {}

#: Import cycles that exist today, each the exact module set of one SCC: reason.
CYCLE_BASELINE: dict[frozenset[str], str] = {
    frozenset({"dokimi.eval.judge_sequential", "dokimi.eval.judge_validity"}):
        "judge_validity imports the SPRT bounds lazily from judge_sequential",
    frozenset({"dokimi.harnesses.exploration", "dokimi.harnesses.models"}):
        "models imports summarize_exploration lazily; exploration needs OperationRecord",
    frozenset({"dokimi.repos.bases", "dokimi.repos.manifest"}):
        "manifest imports check_bases lazily; bases needs RepoCheckReport",
    frozenset({"dokimi.repos.l0.adapters", "dokimi.repos.l0.core"}):
        "core imports adapters lazily; adapters needs Scan from core",
    frozenset({"dokimi.commands.repo", "dokimi.commands.repo_run"}):
        "repo dispatches to repo_run lazily; repo_run reuses repo's manifest helpers",
}

#: Third-party modules only their wrapper imports: banned module -> the wrapper.
BANNED_IMPORTS: dict[str, str] = {
    "anthropic": "dokimi.llm",
    "langchain_anthropic": "dokimi.llm",
    "langchain_openai": "dokimi.llm",
    "litellm": "dokimi.llm",
    "openai": "dokimi.llm",
}

#: Modules importing a banned module anyway, (importer, banned module): reason.
BANNED_BASELINE: dict[tuple[str, str], str] = {
    ("dokimi.eval.ragas_eval", "langchain_anthropic"):
        "ragas takes a LangChain chat model; dokimi.llm has no LangChain adapter",
    ("dokimi.eval.ragas_eval", "langchain_openai"):
        "ragas takes a LangChain chat model; dokimi.llm has no LangChain adapter",
    ("dokimi.questions.validator", "openai"):
        "embeddings for deduplication; dokimi.llm wraps chat completions only",
}

#: The process wrapper; every other module starts a child through it.
SPAWN_WRAPPER = "dokimi.process"
_SPAWNS = {
    "subprocess": {"Popen", "call", "check_call", "check_output", "getoutput",
                   "getstatusoutput", "run"},
    "os": {"execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
           "popen", "posix_spawn", "posix_spawnp", "spawnl", "spawnle", "spawnlp",
           "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "system"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}

#: Modules that still start a child directly: module -> (spawn sites, reason).
#: Empty: the docker-* harnesses start every child through dokimi.process.
SPAWN_BASELINE: dict[str, tuple[int, str]] = {}


# --- the import graph -------------------------------------------------------------

#: Edge kinds, strongest first: a module-level import, one inside a function, one
#: under ``if TYPE_CHECKING:``.
MODULE, LAZY, TYPING = "module", "lazy", "typing"
_RANK = {MODULE: 0, LAZY: 1, TYPING: 2}


@dataclass(frozen=True)
class Imports:
    """One package's imports: first-party module edges and third-party names."""

    modules: frozenset[str]
    #: importer -> imported first-party module -> (kind, line)
    edges: Mapping[str, Mapping[str, tuple[str, int]]]
    #: importer -> top-level third-party (or stdlib) module -> line
    external: Mapping[str, Mapping[str, int]]
    trees: Mapping[str, ast.Module]


def _module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _typing_guard_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to ``TYPE_CHECKING`` and to the ``typing`` module in this file."""
    flags: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "typing" and not node.level:
            flags |= {a.asname or a.name for a in node.names if a.name == "TYPE_CHECKING"}
        elif isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "typing"}
    return flags, modules


def _guarded_by_type_checking(test: ast.expr, flags: set[str], modules: set[str]) -> bool:
    if isinstance(test, ast.Name):
        return test.id in flags
    return (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name) and test.value.id in modules)


def _kinds(tree: ast.Module) -> dict[int, str]:
    """``id(node) -> kind`` for every import that is not at module level."""
    kinds: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                kinds.setdefault(id(inner), LAZY)
    flags, modules = _typing_guard_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _guarded_by_type_checking(node.test, flags, modules):
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    kinds[id(inner)] = TYPING
    return kinds


def _owner_module(name: str, modules: frozenset[str]) -> str | None:
    """The longest prefix of ``name`` that is a module of the package, if any."""
    while name and name not in modules:
        name = name.rpartition(".")[0]
    return name or None


def _import_targets(node: ast.Import | ast.ImportFrom, here: str,
                    modules: frozenset[str]) -> list[str]:
    """The dotted names one import statement reaches, relative imports resolved."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        base = here.split(".")[: len(here.split(".")) - node.level + 1]
        origin = ".".join(base + ([node.module] if node.module else []))
    else:
        origin = node.module or ""
    # `from pkg import sub` depends on pkg.sub; any other name, on pkg.
    subs = [f"{origin}.{a.name}" for a in node.names if f"{origin}.{a.name}" in modules]
    return subs if len(subs) == len(node.names) else [*subs, origin]


def build_imports(root: Path, package: str = PACKAGE) -> Imports:
    """Parse every module under ``root/package``; nothing is imported."""
    paths = {_module_name(p, root): p for p in sorted((root / package).rglob("*.py"))}
    modules = frozenset(paths)
    trees = {m: ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
             for m, p in paths.items()}

    edges: dict[str, dict[str, tuple[str, int]]] = {}
    external: dict[str, dict[str, int]] = {}
    for mod, tree in trees.items():
        here = mod if paths[mod].name == "__init__.py" else mod.rpartition(".")[0]
        kinds = _kinds(tree)
        out = edges.setdefault(mod, {})
        ext = external.setdefault(mod, {})
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            kind = kinds.get(id(node), MODULE)
            for target in _import_targets(node, here, modules):
                if target != package and not target.startswith(package + "."):
                    ext.setdefault(target.partition(".")[0], node.lineno)
                    continue
                dst = _owner_module(target, modules)
                if dst is None or dst == mod:
                    continue
                if dst not in out or _RANK[kind] < _RANK[out[dst][0]]:
                    out[dst] = (kind, node.lineno)
    return Imports(modules, edges, external, trees)


def _pop_component(stack: list[str], on_stack: set[str], root: str) -> set[str]:
    """Pop the Tarjan stack down to ``root``: that is one component."""
    component = set()
    while True:
        w = stack.pop()
        on_stack.discard(w)
        component.add(w)
        if w == root:
            return component


def strongly_connected(imports: Imports, kinds: Iterable[str] = (MODULE, LAZY)
                       ) -> list[frozenset[str]]:
    """Import cycles: every SCC with more than one module (Tarjan)."""
    keep = set(kinds)
    graph = {m: sorted(d for d, (k, _) in imports.edges.get(m, {}).items() if k in keep)
             for m in sorted(imports.modules)}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[frozenset[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = _pop_component(stack, on_stack, v)
            if len(component) > 1:
                found.append(frozenset(component))

    for v in graph:
        if v not in index:
            visit(v)
    return found


# --- the rules --------------------------------------------------------------------


def layer_of(module: str, layers=LAYERS, package: str = PACKAGE) -> str | None:
    """The layer of the longest entry naming ``module`` or a package above it."""
    best: tuple[int, str] | None = None
    for name, entries in layers:
        for entry in entries:
            hit = module == entry or (entry != package and module.startswith(entry + "."))
            if hit and (best is None or len(entry) > best[0]):
                best = (len(entry), name)
    return best[1] if best else None


def layer_violations(imports: Imports, layers=LAYERS) -> dict[tuple[str, str], str]:
    """Every import of a higher layer: (importer, imported) -> description."""
    rank = {name: i for i, (name, _) in enumerate(layers)}
    found = {}
    for src, deps in imports.edges.items():
        src_layer = layer_of(src, layers)
        for dst, (kind, line) in deps.items():
            dst_layer = layer_of(dst, layers)
            if src_layer and dst_layer and rank[dst_layer] < rank[src_layer]:
                found[(src, dst)] = f"{src}:{line} ({src_layer}) imports {dst} ({dst_layer}), {kind}"
    return found


def cycle_problems(found: Iterable[frozenset[str]],
                   baseline: Mapping[frozenset[str], str]) -> list[str]:
    """A cycle not in the baseline, or a baseline entry that is no longer a cycle."""
    found = set(found)
    problems = []
    for cycle in sorted(found - set(baseline), key=sorted):
        grown = [b for b in baseline if b < cycle]
        what = (f"grew from {sorted(min(grown, key=lambda s: (len(s), sorted(s))))}"
                if grown else "is new")
        if any(cycle < b for b in baseline):
            what = "shrank: replace its CYCLE_BASELINE entry with this set"
        problems.append(f"import cycle {sorted(cycle)} {what}")
    for gone in sorted(set(baseline) - found, key=sorted):
        if not any(cycle < gone for cycle in found):
            problems.append(f"baselined cycle {sorted(gone)} is gone: delete it from CYCLE_BASELINE")
    return problems


def banned_imports(imports: Imports, banned=BANNED_IMPORTS) -> dict[tuple[str, str], int]:
    """(importer, banned module) -> line, for every importer but the wrapper."""
    return {(mod, name): line
            for mod, names in imports.external.items()
            for name, line in names.items()
            if name in banned and mod != banned[name]}


def _spawn_imports(tree: ast.Module) -> tuple[dict[str, str], dict[str, tuple[int, str]]]:
    """Module aliases (alias -> module) and directly imported spawn names
    (alias -> (import line, module)) of a file."""
    module_aliases: dict[str, str] = {}
    name_aliases: dict[str, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _SPAWNS:
                    module_aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module in _SPAWNS and not node.level:
            for a in node.names:
                if a.name in _SPAWNS[node.module]:
                    alias = a.asname or a.name
                    # Keep the earliest import (not the latest) for consistent error reporting
                    if alias not in name_aliases:
                        name_aliases[alias] = (node.lineno, node.module)
    return module_aliases, name_aliases


def spawn_sites(tree: ast.Module) -> list[int]:
    """Lines that start a child process without :mod:`dokimi.process`.

    Each launch counts once: an attribute call (``subprocess.run(...)``) or a call
    through a directly imported name (``execute(...)`` after ``from subprocess import
    run as execute``). A directly imported name that is never called counts at its
    import line, since it may be passed around and called there.
    """
    module_aliases, name_aliases = _spawn_imports(tree)
    lines: list[int] = []
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in name_aliases:
            called.add(func.id)
            lines.append(node.lineno)
        elif (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.attr in _SPAWNS.get(module_aliases.get(func.value.id, ""), ())):
            lines.append(node.lineno)
    lines += [line for alias, (line, _) in name_aliases.items() if alias not in called]
    return sorted(lines)


def stale_entries(baseline: Iterable, found: Iterable) -> list:
    """Baseline entries that no longer match anything found: they must be deleted."""
    return sorted(set(baseline) - set(found))


def spawn_problems(imports: Imports, baseline: Mapping[str, tuple[int, str]],
                   wrapper: str = SPAWN_WRAPPER) -> list[str]:
    counts = {m: spawn_sites(t) for m, t in imports.trees.items() if m != wrapper}
    problems = []
    for mod, lines in sorted(counts.items()):
        allowed = baseline.get(mod, (0, ""))[0]
        if len(lines) > allowed:
            problems.append(f"{mod} starts a process at lines {lines}; use {wrapper}.run")
    for mod, (allowed, _) in sorted(baseline.items()):
        now = len(counts.get(mod, []))
        if now < allowed:
            problems.append(f"{mod} has {now} spawn site(s), baseline {allowed}: lower SPAWN_BASELINE")
    return problems


#: Modules that end the process (sys.exit, os._exit, exit, quit, raise SystemExit): only the frontend may.
#: A library raises InputError and cli.main exits 2. Empty: nothing outside the frontend exits.
EXIT_BASELINE: dict[str, tuple[int, str]] = {}
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


#: ``(id of the scope, name) -> [(line, kind, payload)]``. Kinds: ``mod`` (payload: modules), ``fn`` (payload:
#: ``(module, attr)`` of an exit callable), ``sysexit`` (SystemExit, a name for it, or an instance), ``other`` (any other binding, shadowing outer names).
ExitTable = dict[tuple[int, str], list[tuple[int, str, object]]]


def _bind(table: ExitTable, scope: ast.AST, name: str, lineno: int, kind: str, payload: object = None) -> None:
    table.setdefault((id(scope), name), []).append((lineno, kind, payload))


def _import_bindings(node: ast.Import | ast.ImportFrom, scope: ast.AST, table: ExitTable) -> None:
    if isinstance(node, ast.Import):  # ``import sys as s`` binds s; ``import dokimi.exit_codes`` binds that dotted name
        for a in node.names:
            _bind(table, scope, a.asname or a.name, node.lineno, "mod", frozenset({a.name}))
        return
    # ``from .exit_codes import x`` and ``from . import exit_codes`` name dokimi.exit_codes
    module = node.module
    if node.level:
        module = {"exit_codes": EXIT_HELPER, None: "dokimi"}.get(node.module)
    for a in node.names:
        name = a.asname or a.name
        if (module, a.name) in _EXIT_CALLS:  # ``from sys import exit as bye``
            _bind(table, scope, name, node.lineno, "fn", (module, a.name))
        elif f"{module}.{a.name}" in _EXIT_MODULES:  # ``from dokimi import exit_codes as ec``
            _bind(table, scope, name, node.lineno, "mod", frozenset({f"{module}.{a.name}"}))
        elif module == "builtins" and a.name == "SystemExit" and not node.level:  # ``from builtins import SystemExit as Stop``
            _bind(table, scope, name, node.lineno, "sysexit")
        else:
            _bind(table, scope, name, node.lineno, "other")


def _params(args: ast.arguments) -> list[str]:
    named = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    return [a.arg for a in named if a is not None]


def _shadowing_bindings(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable, assigned: set[int]) -> None:
    """Every binding that is not a plain assignment of one name: imports, defs, parameters, loop, with and except targets."""
    bare = {id(n.target) for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and n.value is None}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            _import_bindings(n, owner[id(n)], table)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _bind(table, owner[id(n)], n.name, n.lineno, "other")
        elif isinstance(n, ast.ExceptHandler) and n.name:
            _bind(table, owner[id(n)], n.name, n.lineno, "other")
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and id(n) not in assigned:
            # ``x: int`` binds nothing at run time outside a function, where it makes x local
            if id(n) not in bare or isinstance(owner[id(n)], _FUNC_NODES):
                _bind(table, owner[id(n)], n.id, n.lineno, "other")
        if isinstance(n, _FUNC_NODES):  # a parameter is local to its function
            for name in _params(n.args):
                _bind(table, n, name, n.lineno, "other")


def _value_binding(expr: ast.expr, scope: ast.AST, lineno: int, table: ExitTable, owner: dict[int, ast.AST]):
    """The (kind, payload) that ``x = expr`` gives x: SystemExit, an exit callable, a module, or other."""
    if _is_sysexit_expr(expr, scope, lineno, table, owner):
        return "sysexit", None
    if isinstance(expr, ast.Name):  # ``stop = exit_alias`` copies the binding it names
        b = _resolve(expr.id, scope, lineno, table, owner)
        if b is not None and b[1] in ("fn", "mod"):
            return b[1], b[2]
    if isinstance(expr, (ast.Name, ast.Attribute)):  # ``s = sys`` or ``ec = dokimi.exit_codes``
        mods = _modules(ast.unparse(expr), scope, lineno, table, owner)
        if mods:
            return "mod", frozenset(mods)
    if isinstance(expr, ast.Attribute):  # ``stop = sys.exit``
        for m in _modules(ast.unparse(expr.value), scope, lineno, table, owner):
            if (m, expr.attr) in _EXIT_CALLS:
                return "fn", (m, expr.attr)
    return "other", None


def _assignment_bindings(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable) -> None:
    """``x = <value>`` binds x to what the value is. In source order, so a later value sees the earlier bindings."""
    assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None]
    for n in sorted(assigns, key=lambda n: n.lineno):
        kind, payload = _value_binding(n.value, owner[id(n)], n.lineno, table, owner)
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
            if isinstance(t, ast.Name):
                _bind(table, owner[id(n)], t.id, n.lineno, kind, payload)


def _declared_scopes(tree: ast.Module, owner: dict[int, ast.AST], table: ExitTable) -> None:
    """``global x`` binds x in the module and ``nonlocal x`` in the nearest enclosing scope that binds it."""
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Global, ast.Nonlocal)):
            continue
        scope = owner[id(n)]
        for name in n.names:
            target = tree if isinstance(n, ast.Global) else _binder_above(scope, name, table, owner)
            if target is not None and target is not scope:
                table.setdefault((id(target), name), []).extend(table.pop((id(scope), name), []))


def _binder_above(scope: ast.AST, name: str, table: ExitTable, owner: dict[int, ast.AST]) -> ast.AST | None:
    outer = owner.get(id(scope))
    while outer is not None:
        if (id(outer), name) in table:
            return outer
        outer = owner.get(id(outer))
    return None


def _exit_table(tree: ast.Module, owner: dict[int, ast.AST]) -> ExitTable:
    # the targets of an assignment with a value; ``x: int`` alone binds x (local to its function) as any other name
    assigned = {id(t) for n in ast.walk(tree)
                if isinstance(n, ast.Assign) or (isinstance(n, ast.AnnAssign) and n.value is not None)
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
    table: ExitTable = {}
    _shadowing_bindings(tree, owner, table, assigned)
    _assignment_bindings(tree, owner, table)
    _declared_scopes(tree, owner, table)
    return table


def _resolve(name: str, use: ast.AST, lineno: int, table: ExitTable, owner: dict[int, ast.AST]):
    """The binding that ``name`` denotes at a use on ``lineno`` in scope ``use``, or None when no scope binds it.

    Python's scoping: the innermost scope that binds the name wins. In the use's own scope the last binding at or
    above the use decides, and a binding only below it leaves the name local and unbound there; a class body then
    reads the enclosing names. An enclosing scope is read as its first binding of the name. A class body is seen
    only from its own statements, not from the functions inside it.
    """
    scope = use
    while True:
        if scope is use or not isinstance(scope, ast.ClassDef):
            found = table.get((id(scope), name))
            if found and scope is not use:
                return sorted(found, key=lambda b: b[0])[0]
            if found:
                before = [b for b in found if b[0] <= lineno]
                if before:
                    return sorted(before, key=lambda b: b[0])[-1]
                if not isinstance(use, ast.ClassDef):  # a function's local, not bound yet
                    return (lineno, "other", None)
        if id(scope) not in owner:  # the module: no enclosing scope
            return None
        scope = owner[id(scope)]


def _modules(text: str, scope: ast.AST, lineno: int, table: ExitTable, owner: dict[int, ast.AST]) -> set[str]:
    """The modules a dotted name stands for at this use: an import binding, or sys and os when nothing binds them."""
    b = _resolve(text, scope, lineno, table, owner)
    if b is None:
        return {text} if text in ("sys", "os") else set()
    return set(b[2]) if b[1] == "mod" else set()


def _is_sysexit_expr(expr: ast.expr, scope: ast.AST, lineno: int, table: ExitTable, owner: dict[int, ast.AST]) -> bool:
    """Whether ``expr`` is SystemExit, a name bound to it, or an instance of it (a call of either)."""
    if isinstance(expr, ast.Call):
        return _is_sysexit_expr(expr.func, scope, lineno, table, owner)
    if isinstance(expr, ast.Name):
        b = _resolve(expr.id, scope, lineno, table, owner)
        return expr.id == "SystemExit" if b is None else b[1] == "sysexit"
    if isinstance(expr, ast.Attribute):  # builtins.SystemExit, with ``import builtins``
        return expr.attr == "SystemExit" and "builtins" in _modules(ast.unparse(expr.value), scope, lineno, table, owner)
    return False


def _calls_exit(node: ast.Call, scope: ast.AST, table: ExitTable, owner: dict[int, ast.AST]) -> bool:
    func = node.func
    if isinstance(func, ast.Name):  # exit(), quit(), or a name bound to sys.exit and the like
        b = _resolve(func.id, scope, node.lineno, table, owner)
        return func.id in ("exit", "quit") if b is None else b[1] == "fn" and b[2] in _EXIT_CALLS
    return isinstance(func, ast.Attribute) and any(
        (m, func.attr) in _EXIT_CALLS for m in _modules(ast.unparse(func.value), scope, node.lineno, table, owner))


def exit_sites(tree: ast.Module) -> list[int]:
    """Lines that end the process: an exit call (by module attribute, through an alias, or builtin name) or a raise of SystemExit.

    The ``if __name__ == '__main__':`` block of a script is the process boundary of ``python -m``, so it is
    not counted. Each name resolves by function scope (see ``_resolve``): an import, a parameter or an
    assignment in one function does not change what another function's name means, and a method does not see
    the names of its class body. Linear, not control-flow aware: ``if c: e = SystemExit(2)`` then
    ``else: e = ValueError()`` is read as the else binding.

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
            if _is_sysexit_expr(node.exc, owner[id(node)], node.lineno, table, owner):
                lines.append(node.lineno)
                raised.add(id(node.exc))
        elif isinstance(node, ast.Call) and _calls_exit(node, owner[id(node)], table, owner):
            lines.append(node.lineno)
    return sorted(lines)


def exit_problems(imports: Imports, baseline: Mapping[str, tuple[int, str]],
                  helper: str = EXIT_HELPER) -> list[str]:
    counts = {m: exit_sites(t) for m, t in imports.trees.items()
              if layer_of(m) != "frontend" and m != helper}
    problems = []
    for mod, lines in sorted(counts.items()):
        allowed = baseline.get(mod, (0, ""))[0]
        if len(lines) > allowed:
            problems.append(f"{mod} ends the process at lines {lines}; raise InputError (dokimi.utils) "
                            "and let cli.main exit 2")
    for mod, (allowed, _) in sorted(baseline.items()):
        now = len(counts.get(mod, []))
        if now < allowed:
            problems.append(f"{mod} has {now} exit site(s), baseline {allowed}: lower EXIT_BASELINE")
    return problems


# --- the package ------------------------------------------------------------------


@pytest.fixture(scope="module")
def imports() -> Imports:
    return build_imports(PROJECT)


def test_every_module_is_in_a_layer(imports):
    unplaced = sorted(m for m in imports.modules if layer_of(m) is None)
    assert not unplaced, f"{unplaced} are in no layer: add them to LAYERS in this test"


def test_no_import_crosses_a_layer_upward(imports):
    new = layer_violations(imports)
    added = [desc for edge, desc in sorted(new.items()) if edge not in LAYER_BASELINE]
    assert not added, (
        "imports from a higher layer (see LAYERS in tests/test_import_architecture.py; "
        "frontend modules are imported by no one):\n  " + "\n  ".join(added))


def test_a_fixed_layer_violation_leaves_the_baseline(imports):
    stale = stale_entries(LAYER_BASELINE, layer_violations(imports))
    assert not stale, f"{stale} no longer cross a layer; delete them from LAYER_BASELINE"


def test_import_cycles_do_not_grow(imports):
    problems = cycle_problems(strongly_connected(imports), CYCLE_BASELINE)
    assert not problems, "\n".join(problems)


def test_llm_provider_sdks_go_through_dokimi_llm(imports):
    found = banned_imports(imports)
    added = sorted(f"{m}:{line} imports {name}; use {BANNED_IMPORTS[name]}"
                   for (m, name), line in found.items() if (m, name) not in BANNED_BASELINE)
    stale = stale_entries(BANNED_BASELINE, found)
    assert not added, "\n".join(added)
    assert not stale, f"{stale} no longer import the banned module; delete them from BANNED_BASELINE"


def test_only_the_frontend_ends_the_process(imports):
    problems = exit_problems(imports, EXIT_BASELINE)
    assert not problems, "\n".join(problems)


def test_child_processes_start_through_dokimi_process(imports):
    problems = spawn_problems(imports, SPAWN_BASELINE)
    assert not problems, "\n".join(problems)


def test_baselines_are_explained_and_name_real_modules(imports):
    reasons = [*LAYER_BASELINE.values(), *CYCLE_BASELINE.values(), *BANNED_BASELINE.values(),
               *(reason for _, reason in SPAWN_BASELINE.values()),
               *(reason for _, reason in EXIT_BASELINE.values())]
    assert all(r.strip() for r in reasons), "every baseline entry carries its reason"
    named = {*(m for edge in LAYER_BASELINE for m in edge), *(m for c in CYCLE_BASELINE for m in c),
             *(m for m, _ in BANNED_BASELINE), *SPAWN_BASELINE, *EXIT_BASELINE}
    assert named <= imports.modules, f"{sorted(named - imports.modules)} name no module"


# --- each rule fails on a violation ---------------------------------------------


def _package(tmp_path: Path, files: dict[str, str]) -> Imports:
    for rel, text in {"dokimi/__init__.py": "", **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if path.parent != tmp_path / "dokimi":
            (path.parent / "__init__.py").touch()
    return build_imports(tmp_path)


def test_core_importing_the_frontend_is_a_violation(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/eval/judge.py": "def f():\n    from dokimi.commands import run\n",
        "dokimi/commands/run.py": "",
    })
    assert ("dokimi.eval.judge", "dokimi.commands.run") in layer_violations(imports)


def test_an_upward_import_is_a_violation_even_when_type_only(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/llm.py": ("from typing import TYPE_CHECKING\n"
                          "if TYPE_CHECKING:\n    from dokimi.eval import stats\n"),
        "dokimi/eval/stats.py": "from ..llm import LLM\n",
    })
    found = layer_violations(imports)
    assert set(found) == {("dokimi.llm", "dokimi.eval.stats")}
    assert found[("dokimi.llm", "dokimi.eval.stats")].endswith("typing")


def test_a_new_and_a_grown_cycle_fail_and_a_type_only_one_does_not(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/eval/a.py": "from dokimi.eval import b\n",
        "dokimi/eval/b.py": "def f():\n    from . import a\n",
        "dokimi/eval/c.py": "from dokimi.eval.a import X\n",
        "dokimi/eval/d.py": "import typing\nif typing.TYPE_CHECKING:\n    from . import e\n",
        "dokimi/eval/e.py": "from . import d\n",
    })
    cycles = strongly_connected(imports)
    ab = frozenset({"dokimi.eval.a", "dokimi.eval.b"})
    assert cycles == [ab]
    assert cycle_problems(cycles, {ab: "known"}) == []
    assert "is new" in cycle_problems(cycles, {})[0]
    grown = cycle_problems([ab | {"dokimi.eval.c"}], {ab: "known"})
    assert "grew from" in grown[0]


def test_a_shrunk_or_broken_cycle_must_leave_the_baseline():
    abc = frozenset({"a", "b", "c"})
    assert "shrank" in cycle_problems([frozenset({"a", "b"})], {abc: "known"})[0]
    assert "is gone" in cycle_problems([], {abc: "known"})[0]


def test_a_provider_sdk_outside_the_wrapper_is_banned(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/llm.py": "def f():\n    from litellm import completion\n",
        "dokimi/eval/judge.py": "def f():\n    import litellm.utils\n",
    })
    assert set(banned_imports(imports)) == {("dokimi.eval.judge", "litellm")}


def test_a_direct_spawn_outside_the_wrapper_fails(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/process.py": "import subprocess\nsubprocess.Popen(['x'])\n",
        "dokimi/a.py": "import subprocess as sp\nsp.run(['x'])\nsp.TimeoutExpired\n",
        "dokimi/b.py": "from subprocess import check_output\n",
        "dokimi/c.py": "import os\nos.system('x')\nos.path.join('a')\n",
    })
    problems = spawn_problems(imports, {"dokimi.c": (2, "two sites once")})
    assert [p.split()[0] for p in problems] == ["dokimi.a", "dokimi.b", "dokimi.c"]
    assert "lower SPAWN_BASELINE" in problems[-1]


def test_a_process_exit_outside_the_frontend_fails(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/cli.py": "import sys\nsys.exit(2)\n",
        "dokimi/commands/x.py": "import sys\nsys.exit(1)\n",
        "dokimi/core.py": "import sys\n\ndef f():\n    sys.exit(3)\n",
        "dokimi/b.py": "def g():\n    raise SystemExit('bad')\n",
        "dokimi/c.py": "import os\nos._exit(0)\nexit(1)\n",
        "dokimi/d.py": "import sys\n\nif __name__ == '__main__':\n    sys.exit(0)\n",
        "dokimi/e.py": "import sys\n\nif __name__ == '__main__':\n    pass\nelse:\n    sys.exit(1)\n",
    })
    problems = exit_problems(imports, {"dokimi.c": (1, "one exit once")})
    assert [p.split()[0] for p in problems] == ["dokimi.b", "dokimi.c", "dokimi.core", "dokimi.e"]
    assert "lower EXIT_BASELINE" not in "".join(problems)


def test_an_aliased_process_exit_outside_the_frontend_is_counted(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys as s\ns.exit(1)\n",
        "dokimi/b.py": "from sys import exit as bye\nbye(1)\n",
        "dokimi/c.py": "import os as o\no._exit(0)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [2]


def test_an_exit_through_the_helper_or_a_bound_raise_outside_the_frontend_fails(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/exit_codes.py": "def usage_exit(m):\n    error = SystemExit(m)\n    raise error\n",
        "dokimi/commands/x.py": "from dokimi.exit_codes import usage_exit\nusage_exit('x')\n",
        "dokimi/a.py": "from dokimi.exit_codes import usage_exit as ue\nue('x')\n",
        "dokimi/b.py": "import dokimi.exit_codes\ndokimi.exit_codes.usage_exit('x')\n",
        "dokimi/c.py": "from dokimi import exit_codes as ec\nec.usage_exit('x')\n",
        "dokimi/d.py": "def f():\n    e = SystemExit(2)\n    raise e\n",
        "dokimi/e.py": "import builtins\nbuiltins.exit(1)\n",
        "dokimi/f.py": "import builtins\nraise builtins.SystemExit(2)\n",
        "dokimi/g.py": "from builtins import SystemExit as Stop\nraise Stop(2)\n",
        "dokimi/h.py": "from builtins import SystemExit as Stop\nerror = Stop(2)\nraise error\n",
        "dokimi/i.py": "import builtins\nerror = builtins.SystemExit(2)\nraise error\n",
    })
    problems = exit_problems(imports, {})
    assert [p.split()[0] for p in problems] == ["dokimi.a", "dokimi.b", "dokimi.c", "dokimi.d", "dokimi.e",
                                                "dokimi.f", "dokimi.g", "dokimi.h", "dokimi.i"]
    assert [p.split(" at lines ")[1].split(";")[0] for p in problems] == ["[2]", "[2]", "[2]", "[3]", "[2]",
                                                                         "[2]", "[2]", "[3]", "[3]"]


def test_relative_helper_imports_and_a_reused_module_alias_are_counted(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "from .exit_codes import usage_exit\nusage_exit('x')\n",
        "dokimi/b.py": "from . import exit_codes as ec\nec.usage_exit('x')\n",
        "dokimi/c.py": "def f():\n    import sys as s\n    s.exit(2)\n\ndef g():\n    import os as s\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [3]


def test_annotated_and_scoped_bindings_of_a_raised_exit_are_counted_per_scope(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "error: SystemExit = SystemExit(2)\nraise error\n",
        "dokimi/b.py": ("def f():\n    error = SystemExit(2)\n    return error\n\n"
                        "def g():\n    error = ValueError('bad')\n    raise error\n"),
        "dokimi/c.py": "error = SystemExit(2)\nerror = ValueError('bad')\nraise error\n",
        "dokimi/d.py": "def f():\n    error = SystemExit(3)\n    raise error\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [2]
    assert exit_sites(imports.trees["dokimi.b"]) == []
    assert exit_sites(imports.trees["dokimi.c"]) == []
    assert exit_sites(imports.trees["dokimi.d"]) == [3]


def test_imported_callable_aliases_count_each_launch_once(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/one.py": "from subprocess import run as execute\nexecute(['x'])\n",
        "dokimi/two.py": "from subprocess import run as execute\nexecute(['x'])\nexecute(['y'])\n",
        "dokimi/idle.py": "from subprocess import run\n",
    })
    assert spawn_sites(imports.trees["dokimi.one"]) == [2]  # the call, not the import
    assert spawn_sites(imports.trees["dokimi.two"]) == [2, 3]
    assert spawn_sites(imports.trees["dokimi.idle"]) == [1]  # never called: the import
    baseline = {"dokimi.one": (1, "one"), "dokimi.two": (1, "one"), "dokimi.idle": (1, "one")}
    problems = spawn_problems(imports, baseline)
    assert problems == ["dokimi.two starts a process at lines [2, 3]; use dokimi.process.run"]


def test_stronger_edge_kind_wins_for_duplicate_imports(tmp_path):
    """A type-only import of b followed by a lazy one must leave a lazy edge, so the
    a <-> b cycle is seen; the order of the two imports does not matter."""
    for name, a_src in {
        "typing_first": ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from dokimi import b\n"
                         "def f():\n    from dokimi import b\n"),
        "lazy_first": ("def f():\n    from dokimi import b\n"
                       "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from dokimi import b\n"),
    }.items():
        root = tmp_path / name
        root.mkdir()
        imports = _package(root, {"dokimi/a.py": a_src, "dokimi/b.py": "from dokimi import a\n"})
        assert imports.edges["dokimi.a"]["dokimi.b"][0] == LAZY
        assert strongly_connected(imports) == [frozenset({"dokimi.a", "dokimi.b"})]


def test_a_name_in_one_scope_does_not_change_what_another_scope_means(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "def f():\n    import sys as s\n    s.exit(2)\n\ndef g(s):\n    s.exit()\n",
        "dokimi/b.py": ("def f():\n    from builtins import SystemExit as Stop\n    raise Stop(2)\n\n"
                        "def g():\n    raise Stop(3)\n"),
        "dokimi/c.py": "error = SystemExit(2)\n\ndef f(error):\n    raise error\n\ndef g():\n    raise error\n",
        "dokimi/d.py": ("error = SystemExit(2)\n\nclass C:\n    error = ValueError('bad')\n\n"
                        "    def f(self):\n        raise error\n"),
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [7]
    assert exit_sites(imports.trees["dokimi.d"]) == [7]


def test_an_annotation_alone_makes_a_name_local_to_its_function(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "error = SystemExit(2)\n\ndef f():\n    error: int\n    raise error\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == []


def test_comprehension_targets_and_headers_are_evaluated_where_python_evaluates_them(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\n[x for sys in []]\nsys.exit(2)\n",
        "dokimi/b.py": "import sys\n[x for sys in sys.exit(2)]\n",
        "dokimi/c.py": "import sys\ndef f(sys=sys.exit(2)):\n    pass\n",
        "dokimi/d.py": "import sys\ng = lambda sys=sys.exit(2): 0\n",
        "dokimi/e.py": "import sys\n@sys.exit(2)\ndef f():\n    pass\n",
        "dokimi/f.py": "import sys\nclass C(sys.exit(2)):\n    pass\n",
        "dokimi/g.py": "import sys\ndef f():\n    [sys for sys in []]\n    sys.exit(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [2]
    assert exit_sites(imports.trees["dokimi.c"]) == [2]
    assert exit_sites(imports.trees["dokimi.d"]) == [2]
    assert exit_sites(imports.trees["dokimi.e"]) == [2]
    assert exit_sites(imports.trees["dokimi.f"]) == [2]
    assert exit_sites(imports.trees["dokimi.g"]) == [4]


def test_global_and_nonlocal_declarations_bind_where_they_point(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\ndef f():\n    global sys\n    sys.exit(2)\n    sys = None\n",
        "dokimi/b.py": ("def g():\n    e = SystemExit\n    def f():\n        nonlocal e\n"
                        "        raise e(2)\n        e = None\n    return f\n"),
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [4]
    assert exit_sites(imports.trees["dokimi.b"]) == [5]


def test_a_class_body_reads_enclosing_names_until_it_assigns_them(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\nclass C:\n    sys.exit(2)\n    sys = None\n",
        "dokimi/b.py": "import sys\nsys: object\nsys.exit(2)\n",
        "dokimi/c.py": "import sys\nclass C:\n    sys: object\n    sys.exit(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [4]


def test_an_assigned_exit_callable_or_module_keeps_its_meaning(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "import sys\nstop = sys.exit\nstop(2)\n",
        "dokimi/b.py": "import sys\ns = sys\ns.exit(2)\n",
        "dokimi/c.py": "import sys\nstop: object = sys.exit\nstop(2)\n",
        "dokimi/d.py": "def f():\n    import sys\n    stop = sys.exit\n    stop(2)\n",
        "dokimi/e.py": "import builtins\nstop = builtins.SystemExit\nraise stop(2)\n",
    })
    assert exit_sites(imports.trees["dokimi.a"]) == [3]
    assert exit_sites(imports.trees["dokimi.b"]) == [3]
    assert exit_sites(imports.trees["dokimi.c"]) == [3]
    assert exit_sites(imports.trees["dokimi.d"]) == [4]
    assert exit_sites(imports.trees["dokimi.e"]) == [3]


def test_a_stale_layer_or_banned_baseline_entry_is_reported():
    assert stale_entries({("a", "b"): "x", ("c", "d"): "y"}, {("c", "d"): "z"}) == [("a", "b")]
    assert stale_entries({("m", "litellm"): "x"}, {}) == [("m", "litellm")]
    assert stale_entries({}, {("m", "litellm"): 1}) == []


def test_only_typing_TYPE_CHECKING_guards_against_cycles(tmp_path):
    """A non-typing TYPE_CHECKING attribute (e.g., foo.TYPE_CHECKING) should not
    be treated as a typing guard, so a real runtime import is not hidden from the
    cycle check."""
    imports = _package(tmp_path, {
        "dokimi/a.py": ("class Foo:\n    TYPE_CHECKING = False\n"
                       "if Foo.TYPE_CHECKING:\n    from dokimi import b\n"),
        "dokimi/b.py": "from dokimi import a\n",
    })
    cycles = strongly_connected(imports)
    # The edge from a to b is real (not guarded by typing.TYPE_CHECKING), so the cycle is detected
    assert cycles == [frozenset({"dokimi.a", "dokimi.b"})]


def test_spawn_sites_keeps_earliest_import_for_duplicate_aliases(tmp_path):
    """When the same callable is imported multiple times (e.g., in try/except), keep
    the first import line. If called, report the call line; if never called, report
    the earliest import."""
    imports = _package(tmp_path, {
        "dokimi/proc.py": ("try:\n"
                          "    from subprocess import run as execute\n"
                          "except ImportError:\n"
                          "    from os import system as execute\n"
                          "execute(['x'])\n"),
    })
    # The alias is called, so only the call line [5] is reported (earliest import tracked internally)
    lines = spawn_sites(imports.trees["dokimi.proc"])
    assert lines == [5]


def test_grown_cycle_reports_smallest_proper_subset():
    """When a new cycle is a superset of multiple baselined cycles, report the
    smallest one for clarity (not an arbitrary one dependent on iteration order)."""
    abc = frozenset({"a", "b", "c"})
    ab = frozenset({"a", "b"})
    ac = frozenset({"a", "c"})
    # New cycle is {a, b, c}, which is a superset of both {a, b} and {a, c}
    # Include ab and ac in the found list so they are not reported as gone
    problems = cycle_problems([ab, ac, abc], {ab: "known1", ac: "known2"})
    assert len(problems) == 1
    assert problems == [
        "import cycle ['a', 'b', 'c'] grew from ['a', 'b']"]


def test_aliased_typing_guards_are_typing_only(tmp_path):
    imports = _package(tmp_path, {
        "dokimi/a.py": "from typing import TYPE_CHECKING as TC\nif TC:\n    from dokimi import b\n",
        "dokimi/b.py": "from dokimi import a\n",
        "dokimi/c.py": "import typing as t\nif t.TYPE_CHECKING:\n    from dokimi import d\n",
        "dokimi/d.py": "from dokimi import c\n",
    })
    assert imports.edges["dokimi.a"]["dokimi.b"][0] == TYPING
    assert imports.edges["dokimi.c"]["dokimi.d"][0] == TYPING
    assert strongly_connected(imports) == []


def test_tie_between_equal_size_subsets_is_stable():
    abc = frozenset({"a", "b", "c"})
    ab = frozenset({"a", "b"})
    ac = frozenset({"a", "c"})
    forward = cycle_problems([ab, ac, abc], {ab: "known1", ac: "known2"})
    backward = cycle_problems([ab, ac, abc], {ac: "known2", ab: "known1"})
    assert forward == backward
