#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No-growth ratchet for file length and function complexity.

Two measures over every first-party Python file (``dokimi/``, ``scripts/``,
``tests/`` and ``cli.py``, relative to this project directory):

- **File length** in physical lines. A file under ``dokimi/`` or ``scripts/``
  may have at most ``PROD_LINE_CAP`` lines, a file under ``tests/`` at most
  ``TEST_LINE_CAP``.
- **Cyclomatic complexity** of every function and method, counted the way
  ruff's ``C901`` (mccabe) counts it, computed here with ``ast`` so the gate
  needs nothing beyond the standard library. At most ``COMPLEXITY_CAP``.

Code that was over a cap when the gate was introduced is recorded in
``growth_baseline.json`` at its measured value. A recorded value is exact: the
code may not grow past it, and when the code shrinks the entry must shrink with
it (or go, once the code is back under the cap), so the ceiling never keeps
slack that later growth could quietly use up. Anything new over a cap fails.

``tests/test_growth_ratchet.py`` runs the check in the test job. The modes::

    python scripts/check_growth.py            # check; exit 1 on a violation
    python scripts/check_growth.py --update   # lower or drop entries the code
                                              # outgrew downwards; never raises
    python scripts/check_growth.py --report [--markdown] [--json PATH]
                                              # totals and the most complex functions

Raising an entry (or adding one) is a hand edit of the baseline with a
``"rationale"`` of at most ``RATIONALE_MAX`` characters on that entry; the test
compares the baseline against the PR's base branch and fails a raised or added
entry without one.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
BASELINE = PROJECT / "scripts" / "growth_baseline.json"

#: Production code (``dokimi/``, ``scripts/``, ``cli.py``). When the gate was added
#: 6 of 224 files were over 800 lines; the next largest had 766.
PROD_LINE_CAP = 800
#: Tests group many cases per module: 8 of 163 were over 1000 lines, 3 over 1200.
TEST_LINE_CAP = 1000
#: Per-function cyclomatic complexity, as ruff's C901 counts it. 10 is mccabe's and
#: ruff's default ``max-complexity``, so C901 can later be switched on in ruff with
#: the same number; 81 of 6354 functions were over it when the gate was added.
COMPLEXITY_CAP = 10
#: A raised or added baseline entry says why in at most this many characters.
RATIONALE_MAX = 300

ROOTS = ("dokimi", "scripts", "tests")
TOP_FILES = ("cli.py",)
FILES, FUNCTIONS = "files", "functions"
METRIC = {FILES: "lines", FUNCTIONS: "complexity"}


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------


def python_files(project: Path) -> list[Path]:
    """Every first-party .py file, sorted; caches and dot-directories skipped."""
    found = [project / name for name in TOP_FILES if (project / name).is_file()]
    for root in ROOTS:
        for path in (project / root).rglob("*.py"):
            parts = path.relative_to(project).parts
            if any(p == "__pycache__" or p.startswith(".") for p in parts):
                continue
            found.append(path)
    return sorted(found)


def line_cap(rel: str) -> int:
    return TEST_LINE_CAP if rel.startswith("tests/") else PROD_LINE_CAP


def _is_irrefutable(pattern: ast.pattern) -> bool:
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _is_irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_is_irrefutable(p) for p in pattern.patterns)
    return False


def _complexity(stmts: list[ast.stmt]) -> int:
    """mccabe's decision count for a statement list, as ruff's C901 adds it up:
    if/elif, for, while, each except handler, a try's else, each match case
    (less an irrefutable last one) and each nested def score one. Boolean
    operators and comprehensions score nothing."""
    n = 0
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            n += 1 + _complexity(stmt.body) + _complexity(stmt.orelse)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            n += 1 + _complexity(stmt.body) + _complexity(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            n += _complexity(stmt.body)
        elif isinstance(stmt, ast.Match):
            n += sum(1 + _complexity(case.body) for case in stmt.cases)
            last = stmt.cases[-1] if stmt.cases else None
            if last is not None and last.guard is None and _is_irrefutable(last.pattern):
                n -= 1
        elif isinstance(stmt, (ast.Try, *((ast.TryStar,) if hasattr(ast, "TryStar") else ()))):
            n += _complexity(stmt.body) + _complexity(stmt.finalbody)
            n += (1 if stmt.orelse else 0) + _complexity(stmt.orelse)
            n += sum(1 + _complexity(h.body) for h in stmt.handlers)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n += 1 + _complexity(stmt.body)
        elif isinstance(stmt, ast.ClassDef):
            n += _complexity(stmt.body)
    return n


def function_complexities(tree: ast.Module) -> dict[str, int]:
    """``qualified.name -> complexity`` for every def, nested ones included. A
    nested def counts in its own entry and in its parent's, as in ruff. Two defs
    with one name (a property setter, a conditional redefinition) keep the max."""
    out: dict[str, int] = {}

    def visit(stmts: list[ast.stmt], prefix: str) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + stmt.name
                out[name] = max(out.get(name, 0), 1 + _complexity(stmt.body))
                visit(stmt.body, name + ".")
            elif isinstance(stmt, ast.ClassDef):
                visit(stmt.body, prefix + stmt.name + ".")
            else:
                # defs inside if/for/while/with/try/match blocks
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.stmt):
                        visit([child], prefix)
                    elif isinstance(child, (ast.excepthandler, ast.match_case)):
                        visit(child.body, prefix)

    visit(tree.body, "")
    return out


class Measurement:
    """Lines per file (``path``) and complexity per function (``path::qualname``)."""

    def __init__(self, lines: dict[str, int] | None = None,
                 complexity: dict[str, int] | None = None) -> None:
        self.lines = dict(lines or {})
        self.complexity = dict(complexity or {})

    def over_cap(self) -> dict[str, dict[str, int]]:
        return {
            FILES: {p: n for p, n in self.lines.items() if n > line_cap(p)},
            FUNCTIONS: {k: n for k, n in self.complexity.items() if n > COMPLEXITY_CAP},
        }


def measure(project: Path | None = None) -> Measurement:
    project = project or PROJECT
    m = Measurement()
    for path in python_files(project):
        rel = path.relative_to(project).as_posix()
        text = path.read_text(encoding="utf-8")
        m.lines[rel] = len(text.splitlines())
        for name, n in function_complexities(ast.parse(text, filename=rel)).items():
            m.complexity[f"{rel}::{name}"] = n
    return m


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def load_baseline(path: Path | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    data = json.loads((path or BASELINE).read_text(encoding="utf-8"))
    return {FILES: data.get(FILES, {}), FUNCTIONS: data.get(FUNCTIONS, {})}


def dump_baseline(baseline: dict[str, dict[str, dict[str, Any]]]) -> str:
    """One entry per line, keys sorted: a PR's change to an entry is a one-line diff."""
    out = ["{"]
    for i, section in enumerate((FILES, FUNCTIONS)):
        entries = sorted(baseline[section].items())
        out.append(f'  "{section}": {{' + ("" if entries else "}" + ("," if i == 0 else "")))
        for j, (key, entry) in enumerate(entries):
            comma = "," if j < len(entries) - 1 else ""
            out.append(f"    {json.dumps(key)}: {json.dumps(entry, sort_keys=True)}{comma}")
        if entries:
            out.append("  }" + ("," if i == 0 else ""))
    out.append("}")
    return "\n".join(out) + "\n"


def _entry_problem(key: str, entry: dict[str, Any], section: str, now: int | None,
                   over: bool) -> str | None:
    """What is wrong with one baseline entry, given the code's current value."""
    metric = METRIC[section]
    recorded = entry.get(metric)
    rationale = entry.get("rationale", "")
    update = "(python scripts/check_growth.py --update)"
    if not isinstance(recorded, int):
        return f"{key}: baseline entry has no integer {metric!r}"
    if not isinstance(rationale, str) or len(rationale) > RATIONALE_MAX:
        return f"{key}: rationale must be a string of at most {RATIONALE_MAX} characters"
    if now is None:
        what = "file" if section == FILES else "function"
        return f"{key}: {what} no longer exists; remove its baseline entry {update}"
    if not over:
        return f"{key}: {now} {metric}, back under the cap; remove its baseline entry {update}"
    if now > recorded:
        return (f"{key}: {now} {metric} grew past its baseline {recorded}; "
                f"split it instead (see {BASELINE.name})")
    if now < recorded:
        return (f"{key}: {now} {metric}, below its baseline {recorded}; lower the entry "
                f"so the gain stays {update}")
    return None


def violations(m: Measurement, baseline: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """Everything the gate fails on, one message per finding."""
    found: list[str] = []
    actual = {FILES: m.lines, FUNCTIONS: m.complexity}
    over = m.over_cap()
    for section in (FILES, FUNCTIONS):
        for key, entry in sorted(baseline[section].items()):
            problem = _entry_problem(key, entry, section, actual[section].get(key),
                                     key in over[section])
            if problem:
                found.append(problem)
        for key, now in sorted(over[section].items()):
            if key not in baseline[section]:
                cap = line_cap(key) if section == FILES else COMPLEXITY_CAP
                fix = "split the file" if section == FILES else "split the function into helpers"
                found.append(f"{key}: {now} {METRIC[section]} is over the {cap} cap and has "
                             f"no baseline entry; {fix}")
    return found


def raised_without_rationale(baseline: dict[str, dict[str, dict[str, Any]]],
                             base: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """Entries added or raised relative to ``base`` (the base branch's baseline)
    that carry no new rationale: an entry already explained at the old value needs
    its rationale rewritten to cover the raise."""
    found = []
    for section in (FILES, FUNCTIONS):
        metric = METRIC[section]
        for key, entry in sorted(baseline[section].items()):
            old = base[section].get(key, {})
            before = old.get(metric)
            value = entry.get(metric)
            if before is not None and isinstance(value, int) and value <= before:
                continue
            rationale = str(entry.get("rationale", "")).strip()
            if not rationale or (before is not None and rationale == str(old.get("rationale", "")).strip()):
                change = "added" if before is None else f"raised from {before} to {value}"
                found.append(f"{key}: {change} without a \"rationale\" (at most "
                             f"{RATIONALE_MAX} characters: why it cannot be split now)")
    return found


def base_ref() -> str:
    """The branch a PR merges into: ``DOKIMI_GROWTH_BASE``, else ``origin/`` plus
    GitHub's ``GITHUB_BASE_REF`` (set on pull_request runs), else ``origin/main``."""
    if os.environ.get("DOKIMI_GROWTH_BASE"):
        return os.environ["DOKIMI_GROWTH_BASE"]
    return "origin/" + (os.environ.get("GITHUB_BASE_REF") or "main")


def load_base_baseline(ref: str) -> dict[str, dict[str, dict[str, Any]]] | None:
    """The baseline as ``ref`` has it, or None when git, the ref or the file there is
    missing (a shallow clone, or the base predates the gate)."""
    try:
        shown = subprocess.run(
            ["git", "show", f"{ref}:./{BASELINE.relative_to(PROJECT).as_posix()}"],
            cwd=PROJECT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    if shown.returncode != 0:
        return None
    data = json.loads(shown.stdout)
    return {FILES: data.get(FILES, {}), FUNCTIONS: data.get(FUNCTIONS, {})}


def tightened(m: Measurement, baseline: dict[str, dict[str, dict[str, Any]]]
              ) -> dict[str, dict[str, dict[str, Any]]]:
    """The baseline with every entry lowered to its current value, and entries
    whose code is gone or back under the cap removed. Nothing is raised or added."""
    actual = {FILES: m.lines, FUNCTIONS: m.complexity}
    over = m.over_cap()
    new: dict[str, dict[str, dict[str, Any]]] = {FILES: {}, FUNCTIONS: {}}
    for section in (FILES, FUNCTIONS):
        metric = METRIC[section]
        for key, entry in baseline[section].items():
            if key not in over[section]:
                continue
            recorded = entry.get(metric)
            new[section][key] = dict(entry)
            if isinstance(recorded, int):
                new[section][key][metric] = min(recorded, actual[section][key])
    return new


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def report(m: Measurement, top: int = 10) -> dict[str, Any]:
    over = m.over_cap()
    buckets = Counter()
    edges = sorted({5, 10, COMPLEXITY_CAP, 20, 30, 50})
    for n in m.complexity.values():
        label = next((f"<={e}" for e in edges if n <= e), f">{edges[-1]}")
        buckets[label] += 1
    order = [f"<={e}" for e in edges] + [f">{edges[-1]}"]
    return {
        "caps": {"prod_lines": PROD_LINE_CAP, "test_lines": TEST_LINE_CAP,
                 "complexity": COMPLEXITY_CAP},
        "files": len(m.lines),
        "lines": sum(m.lines.values()),
        "files_over_cap": len(over[FILES]),
        "lines_over_cap": sum(n - line_cap(p) for p, n in over[FILES].items()),
        "functions": len(m.complexity),
        "functions_over_cap": len(over[FUNCTIONS]),
        "complexity_over_cap": sum(n - COMPLEXITY_CAP for n in over[FUNCTIONS].values()),
        "complexity_distribution": {k: buckets.get(k, 0) for k in order},
        "largest_files": sorted(m.lines.items(), key=lambda kv: (-kv[1], kv[0]))[:top],
        "most_complex": sorted(m.complexity.items(), key=lambda kv: (-kv[1], kv[0]))[:top],
    }


def render(r: dict[str, Any], markdown: bool) -> str:
    caps = r["caps"]
    head = [
        f"files: {r['files']} ({r['lines']} lines); over the line cap "
        f"(prod {caps['prod_lines']}, tests {caps['test_lines']}): {r['files_over_cap']}, "
        f"{r['lines_over_cap']} lines over in total",
        f"functions: {r['functions']}; over complexity {caps['complexity']}: "
        f"{r['functions_over_cap']}, {r['complexity_over_cap']} points over in total",
        "complexity distribution: " + ", ".join(
            f"{k}: {v}" for k, v in r["complexity_distribution"].items()),
    ]
    if not markdown:
        lines = head + ["", "most complex:"]
        lines += [f"  {n:4d}  {k}" for k, n in r["most_complex"]]
        lines += ["", "largest files:"]
        lines += [f"  {n:5d}  {k}" for k, n in r["largest_files"]]
        return "\n".join(lines) + "\n"
    lines = ["### File length and complexity (scripts/check_growth.py)", ""]
    lines += [f"- {h}" for h in head]
    lines += ["", "| complexity | function |", "|---:|---|"]
    lines += [f"| {n} | `{k}` |" for k, n in r["most_complex"]]
    lines += ["", "| lines | file |", "|---:|---|"]
    lines += [f"| {n} | `{k}` |" for k, n in r["largest_files"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true",
                      help="lower or remove baseline entries to match the code; never raises")
    mode.add_argument("--report", action="store_true",
                      help="print totals and the top offenders; always exits 0")
    ap.add_argument("--markdown", action="store_true", help="--report as Markdown")
    ap.add_argument("--json", type=Path, metavar="PATH", help="--report: also write JSON here")
    ap.add_argument("--top", type=int, default=10, help="--report: rows per table")
    ap.add_argument("--base", default=None,
                    help="ref whose baseline a raised entry is compared with "
                         "(default: DOKIMI_GROWTH_BASE, origin/$GITHUB_BASE_REF, origin/main)")
    args = ap.parse_args(argv)

    m = measure()
    if args.report:
        r = report(m, args.top)
        sys.stdout.write(render(r, args.markdown))
        if args.json:
            args.json.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
        return 0
    baseline = load_baseline()
    if args.update:
        BASELINE.write_text(dump_baseline(tightened(m, baseline)), encoding="utf-8")
        remaining = violations(m, load_baseline())
        print(f"wrote {BASELINE.relative_to(PROJECT)}; {len(remaining)} violation(s) left")
        for v in remaining:
            print(f"  {v}")
        return 1 if remaining else 0
    found = violations(m, baseline)
    ref = args.base or base_ref()
    base = load_base_baseline(ref)
    if base is None:
        print(f"note: no {BASELINE.name} at {ref}; raised entries not checked for a rationale")
    else:
        found += raised_without_rationale(baseline, base)
    for v in found:
        print(v)
    if found:
        print(f"\n{len(found)} growth violation(s); see the docstring of scripts/check_growth.py")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
