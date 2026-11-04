# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A command refuses bad input by raising InputError; cli.main prints it and exits 2.

A literal ``sys.exit(2)`` or ``SystemExit(2)`` in the frontend is that refusal written the old
way (its message is printed by hand, with no ``Error:`` prefix from one place). The ratchet caps
the remaining sites per module; each entry in EXIT2_BASELINE says why it is not an input error.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "dokimi"

#: Literal exits with 2 that stay: a run that failed (not a refusal before it), or the one handler.
EXIT2_BASELINE: dict[str, tuple[int, str]] = {
    "dokimi.cli": (1, "the handler: prints InputError as 'Error: ...' and exits 2"),
    "dokimi.commands.evaluate": (1, "typed scorer: ImportError (a missing backend) shares the except with ValueError"),
    "dokimi.commands.ingest": (1, "Docling not installed: a missing optional dependency, not an input"),
    "dokimi.commands.tools": (2, "'run incomplete' exit documented in docs/quickstart.md, not a refusal"),
}


def _is_exit(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "exit" and isinstance(func.value, ast.Name) and func.value.id == "sys"
    return isinstance(func, ast.Name) and func.id in {"SystemExit", "exit"}


def literal_exit_2_lines(source: str) -> list[int]:
    """The lines of ``sys.exit(2)``, ``SystemExit(2)`` and ``exit(2)`` calls with the literal 2."""
    return sorted(
        node.lineno for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _is_exit(node.func) and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == 2
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
        ("import sys\nsys.exit(1)\nsys.exit(EXIT_USAGE)\n", []),
        ("import sys\nsys.exit(code)\n", []),
        ("error = SystemExit(3)\n", []),
    ],
    ids=["sys-exit", "system-exit-from-none", "builtin-exit", "other-codes-and-names", "variable", "other-code"],
)
def test_the_ratchet_counts_literal_exit_2_only(source, lines):
    assert literal_exit_2_lines(source) == lines


def test_a_new_literal_exit_2_in_a_command_fails_the_ratchet(tmp_path):
    path = tmp_path / "new.py"
    path.write_text("import sys\n\ndef run():\n    sys.exit(2)\n", encoding="utf-8")
    problems = literal_exit_2_problems({"dokimi.commands.new": path}, {})
    assert problems and "dokimi.commands.new exits 2 at lines [4]" in problems[0]
