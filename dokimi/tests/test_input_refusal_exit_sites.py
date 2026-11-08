# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A command refuses bad input by raising InputError; cli.main prints it and exits 2.

An exit with code 2 in the frontend (a literal, ``os._exit``, ``exit``, a constant holding 2 such as
``EXIT_USAGE``, or a ``usage_exit(...)`` call) is that refusal written the old way: its message is
printed by hand, with no ``Error:`` prefix from one place. The ratchet caps the remaining sites per
module; each entry in EXIT2_BASELINE says why it stays, or that it is not converted yet.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "dokimi"

#: Exits with 2 that stay: the handler, a dependency, a documented exit, and the
#: ``EXIT_USAGE`` refusals not yet converted (#275 follow-up). Lower a count when a site converts.
_UNCONVERTED = "EXIT_USAGE refusal written by hand, not an InputError yet (#275 follow-up)"
EXIT2_BASELINE: dict[str, tuple[int, str]] = {
    "dokimi.cli": (1, "the handler: prints InputError as 'Error: ...' and exits 2"),
    "dokimi.commands._output": (1, "write_text_or_exit: shared writer that exits 2 by hand (#275 follow-up)"),
    "dokimi.commands.answers": (8, _UNCONVERTED),
    "dokimi.commands.arms": (11, _UNCONVERTED),
    "dokimi.commands.baseline": (4, _UNCONVERTED),
    "dokimi.commands.benchmark": (5, _UNCONVERTED),
    "dokimi.commands.dashboard": (2, _UNCONVERTED),
    "dokimi.commands.eval_coverage": (7, _UNCONVERTED),
    "dokimi.commands.evaluate": (5, "typed scorer: ImportError (a missing backend) shares the except with ValueError; "
                                    "4 EXIT_USAGE refusals not yet converted"),
    "dokimi.commands.explore": (1, "1 EXIT_USAGE refusal not yet converted (#275 follow-up)"),
    "dokimi.commands.ingest": (1, "Docling not installed: a missing optional dependency, not an input"),
    "dokimi.commands.judge_reliability": (2, _UNCONVERTED),
    "dokimi.commands.library": (4, _UNCONVERTED),
    "dokimi.commands.orchestrate": (3, _UNCONVERTED),
    "dokimi.commands.personas": (9, _UNCONVERTED),
    "dokimi.commands.questions": (14, _UNCONVERTED),
    "dokimi.commands.repo": (20, _UNCONVERTED),
    "dokimi.commands.repo_hits": (6, _UNCONVERTED),
    "dokimi.commands.repo_run": (4, _UNCONVERTED),
    "dokimi.commands.repo_scan": (4, _UNCONVERTED),
    "dokimi.commands.repo_skill_reach": (1, _UNCONVERTED),
    "dokimi.commands.report": (13, _UNCONVERTED),
    "dokimi.commands.run": (5, _UNCONVERTED),
    "dokimi.commands.subjects": (7, "7 EXIT_USAGE refusals not yet converted (#275 follow-up)"),
    "dokimi.commands.tools": (5, "'run incomplete' exit (2 literal) documented in docs/quickstart.md, not a refusal; "
                                 "3 EXIT_USAGE refusals not yet converted"),
}


#: Calls that end the process: ``sys.exit``, ``os._exit``, ``builtins.exit``/``quit``, and the bare names.
_EXIT_ATTRS = {("sys", "exit"), ("os", "_exit"), ("builtins", "exit"), ("builtins", "quit")}
_EXIT_NAMES = {"SystemExit", "exit", "quit"}


def _is_exit(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return isinstance(func.value, ast.Name) and (func.value.id, func.attr) in _EXIT_ATTRS
    return isinstance(func, ast.Name) and func.id in _EXIT_NAMES


def _exit_code_constants() -> dict[str, int]:
    """The integer constants of dokimi/exit_codes.py (EXIT_USAGE = 2, ...), read without importing it."""
    consts: dict[str, int] = {}
    for node in ast.parse((PACKAGE / "exit_codes.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            _bind_int(consts, node.targets[0], node.value)
    return consts


def _bind_int(consts: dict[str, int], target: ast.expr, value: ast.expr | None) -> None:
    """``NAME = 2`` records the constant; a rebinding to anything else drops it."""
    if isinstance(target, ast.Name):
        if isinstance(value, ast.Constant) and isinstance(value.value, int):
            consts[target.id] = value.value
        else:
            consts.pop(target.id, None)


def _module_int_constants(tree: ast.Module) -> dict[str, int]:
    """Module-level names bound to an int literal, and names imported from an ``exit_codes`` module."""
    shared = _exit_code_constants()
    consts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "exit_codes":
            consts.update({a.asname or a.name: shared[a.name] for a in node.names if a.name in shared})
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            _bind_int(consts, node.targets[0], node.value)
        elif isinstance(node, ast.AnnAssign):
            _bind_int(consts, node.target, node.value)
    return consts


def _code_value(arg: ast.expr, consts: dict[str, int]) -> object:
    if isinstance(arg, ast.Constant):
        return arg.value
    return consts.get(arg.id) if isinstance(arg, ast.Name) else None


def _is_usage_exit(func: ast.expr) -> bool:
    """A call named ``usage_exit`` (dokimi.exit_codes, matched by name): prints by hand and exits 2."""
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    return name == "usage_exit"


def literal_exit_2_lines(source: str) -> list[int]:
    """The lines of an exit call whose code is 2: the literal, or a module-level name that holds 2.

    Covers ``sys.exit``, ``os._exit``, ``exit``/``quit``, ``SystemExit`` (raised or not), and
    ``usage_exit(...)``, which always exits 2. Not resolved:
    attribute access (``exit_codes.EXIT_USAGE``) and names bound inside a function or an ``if``.
    """
    tree = ast.parse(source)
    consts = _module_int_constants(tree)
    return sorted(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (
            _is_usage_exit(node.func)
            or (_is_exit(node.func) and node.args and _code_value(node.args[0], consts) == 2)
        )
    )


def _frontend_modules() -> dict[str, Path]:
    paths = [PACKAGE / "cli.py", PACKAGE / "cli_routes.py", *sorted((PACKAGE / "commands").glob("*.py"))]
    return {".".join(p.relative_to(PACKAGE.parent).with_suffix("").parts): p for p in paths}


def literal_exit_2_problems(modules: dict[str, Path], baseline: dict[str, tuple[int, str]]) -> list[str]:
    problems = []
    for mod, path in sorted(modules.items()):
        lines = literal_exit_2_lines(path.read_text(encoding="utf-8"))
        allowed = baseline.get(mod, (0, ""))[0]
        if len(lines) > allowed:
            problems.append(f"{mod} exits 2 at lines {lines}; raise InputError (dokimi.utils) with the message "
                            "and let cli.main print it and exit 2")
        elif len(lines) < allowed:
            problems.append(f"{mod} has {len(lines)} literal exit(s) 2, baseline {allowed}: lower EXIT2_BASELINE")
    return problems


def test_a_command_refuses_bad_input_by_raising_input_error():
    problems = literal_exit_2_problems(_frontend_modules(), EXIT2_BASELINE)
    assert not problems, "\n".join(problems)


def test_baseline_entries_are_explained_and_name_real_modules():
    assert all(reason.strip() for _, reason in EXIT2_BASELINE.values()), "every entry carries its reason"
    assert set(EXIT2_BASELINE) <= set(_frontend_modules()), "every entry names a frontend module"


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        ("import sys\nsys.exit(2)\n", [2]),
        ("raise SystemExit(2) from None\n", [1]),
        ("exit(2)\n", [1]),
        ("import os\nos._exit(2)\n", [2]),
        ("import sys\nCODE = 2\nsys.exit(CODE)\n", [3]),
        ("EXIT_BAD: int = 2\nraise SystemExit(EXIT_BAD)\n", [2]),
        ("from dokimi.exit_codes import EXIT_USAGE\nraise SystemExit(EXIT_USAGE)\n", [2]),
        ("from ..exit_codes import EXIT_USAGE as USAGE\nimport sys\nsys.exit(USAGE)\n", [3]),
        ("import sys\nsys.exit(1)\nsys.exit(EXIT_FAILURE)\n", []),
        ("import sys\nsys.exit(code)\n", []),
        ("import sys\nCODE = 3\nsys.exit(CODE)\n", []),
        ("error = SystemExit(3)\n", []),
        ("from dokimi.exit_codes import usage_exit\nusage_exit(\"Error: x\")\n", [2]),
        ("from dokimi import exit_codes\nexit_codes.usage_exit(\"Error: x\")\n", [2]),
    ],
    ids=["sys-exit", "system-exit-from-none", "builtin-exit", "os-exit", "module-constant",
         "annotated-module-constant", "imported-exit-usage", "relative-import-alias", "exit-1-by-name",
         "variable", "other-constant", "other-code", "usage-exit", "usage-exit-attribute"],
)
def test_the_ratchet_counts_every_spelling_of_exit_2(source, lines):
    assert literal_exit_2_lines(source) == lines


def test_a_new_literal_exit_2_in_a_command_fails_the_ratchet(tmp_path):
    path = tmp_path / "new.py"
    path.write_text("import sys\n\ndef run():\n    sys.exit(2)\n", encoding="utf-8")
    problems = literal_exit_2_problems({"dokimi.commands.new": path}, {})
    assert problems and "dokimi.commands.new exits 2 at lines [4]" in problems[0]


#: One fixture module with every spelling of an exit 2 the ratchet must count, one per line.
_EVERY_SPELLING = """\
import os
import sys
from dokimi.exit_codes import EXIT_USAGE

CODE = 2


def run():
    sys.exit(2)
    raise SystemExit(2)
    exit(2)
    os._exit(2)
    sys.exit(CODE)
    raise SystemExit(EXIT_USAGE)
"""


def test_a_fixture_module_with_every_spelling_of_exit_2_is_flagged(tmp_path):
    path = tmp_path / "every.py"
    path.write_text(_EVERY_SPELLING, encoding="utf-8")
    problems = literal_exit_2_problems({"dokimi.commands.every": path}, {})
    assert problems and "dokimi.commands.every exits 2 at lines [9, 10, 11, 12, 13, 14]" in problems[0]
