# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""No new fixed-duration wait in ``tests/``.

A test that waits for a thread, process or timer with ``time.sleep`` or ``threading.Timer`` passes
on a fast machine and fails on a busy runner: wait on the condition with ``tests/waiting.py``
instead. This counts, per file under ``tests/``, the calls the file's imports resolve to
``time.sleep`` or ``threading.Timer`` (``import time``, ``import time as t``, ``from time import
sleep as nap``, and the same for ``threading``). A ``sleep`` in a string, such as a child's shell
command, is not a call. ``tests/waiting.py`` is the one file not counted.

The counts are recorded in ``test_timing_baseline.json`` and are exact: a file may not go above its
entry, and when it goes below, the entry shrinks with it. A call that must stay ends its line with
``# timing-ok: <reason>``; a ``# timing-ok`` with no reason fails::

    python scripts/check_test_timing.py            # check; exit 1 on a problem
    python scripts/check_test_timing.py --update   # lower or drop entries; never raises (writes
                                                   # a first baseline when the file is missing)
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"
BASELINE = PROJECT / "scripts" / "test_timing_baseline.json"

#: The helper module is where the polling loop lives, so it alone may sleep.
EXEMPT = "waiting.py"
TARGETS = {"time": {"sleep"}, "threading": {"Timer"}}
WAIVER = re.compile(r"#\s*timing-ok\b(?::(?P<reason>.*))?$")
HINT = "(python scripts/check_test_timing.py --update)"


def _bindings(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """``(module aliases, function aliases)``: ``t -> time`` and ``nap -> time.sleep``."""
    modules: dict[str, str] = {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in TARGETS:
                    modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in TARGETS and node.level == 0:
            for alias in node.names:
                if alias.name in TARGETS[node.module]:
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return modules, names


def _resolve(call: ast.Call, modules: dict[str, str], names: dict[str, str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return names.get(func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = modules.get(func.value.id)
        if module and func.attr in TARGETS[module]:
            return f"{module}.{func.attr}"
    return None


def _waivers(text: str) -> tuple[set[int], list[int]]:
    """Lines with a ``# timing-ok: <reason>`` comment, and lines with one that has no reason."""
    ok: set[int] = set()
    bare: list[int] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        match = WAIVER.match(tok.string) if tok.type == tokenize.COMMENT else None
        if match is None:
            continue
        if re.search(r"\w", match.group("reason") or ""):
            ok.add(tok.start[0])
        else:
            bare.append(tok.start[0])
    return ok, bare


def scan(text: str, filename: str) -> tuple[dict[str, int], list[int]]:
    """``({"time.sleep": n, "threading.Timer": m}, bare waiver lines)`` of one file's source."""
    tree = ast.parse(text, filename=filename)
    modules, names = _bindings(tree)
    ok, bare = _waivers(text)
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve(node, modules, names)
        lines = {node.lineno, node.end_lineno or node.lineno}
        if target and not lines & ok:
            counts[target] = counts.get(target, 0) + 1
    return counts, bare


def measure(root: Path) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """``(calls per file, calls per target, problems)`` over every ``*.py`` under ``root``."""
    per_file: dict[str, int] = {}
    per_target: dict[str, int] = {}
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if path.relative_to(root).as_posix() == EXEMPT or any(
                p == "__pycache__" or p.startswith(".") for p in parts):
            continue
        key = f"{root.name}/{path.relative_to(root).as_posix()}"
        counts, bare = scan(path.read_text(encoding="utf-8"), key)
        problems += [f"{key}:{line}: `# timing-ok` needs a reason: `# timing-ok: <why this sleep must stay>`"
                     for line in bare]
        if counts:
            per_file[key] = sum(counts.values())
        for target, n in counts.items():
            per_target[target] = per_target.get(target, 0) + n
    return per_file, per_target, problems


def load_baseline(path: Path) -> dict[str, int]:
    return {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).get("files", {}).items()}


def dump_baseline(files: dict[str, int]) -> str:
    return json.dumps({"files": dict(sorted(files.items()))}, indent=2) + "\n"


def violations(now: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """``(above, below)``: files over their entry (or with none), and files under it."""
    above: list[str] = []
    below: list[str] = []
    for key in sorted(set(now) | set(baseline)):
        have, entry = now.get(key, 0), baseline.get(key)
        if have > (entry or 0):
            was = "no baseline entry" if entry is None else f"its baseline {entry}"
            above.append(f"{key}: {have} sleep/Timer call(s), above {was}; wait on the condition with "
                         "tests/waiting.py, or end the line with `# timing-ok: <reason>`")
        elif entry is not None and have < entry:
            below.append(f"{key}: {have} sleep/Timer call(s), below its baseline {entry}; lower the "
                         f"entry so the gain stays {HINT}")
    return above, below


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--update", action="store_true",
                    help="lower or drop baseline entries to match the code; never raises one")
    ap.add_argument("--tests-root", type=Path, default=TESTS, help="directory to scan (default: tests/)")
    ap.add_argument("--baseline", type=Path, default=BASELINE, help="baseline file to read and write")
    args = ap.parse_args(argv)

    per_file, per_target, problems = measure(args.tests_root)
    summary = (f"{sum(per_file.values())} sleep/Timer call(s) in {len(per_file)} file(s): "
               + ", ".join(f"{n} {t}" for t, n in sorted(per_target.items())))
    if args.update and not args.baseline.exists() and not problems:
        args.baseline.write_text(dump_baseline(per_file), encoding="utf-8")
        print(f"wrote {args.baseline.name}; {summary}")
        return 0
    if not args.baseline.exists():
        print(f"no {args.baseline.name}; create it with --update {HINT}")
        return 1
    baseline = load_baseline(args.baseline)
    above, below = violations(per_file, baseline)
    if args.update:
        found = problems + above
        if found:
            print(f"refusing to update {args.baseline.name}: it only shrinks")
            print("\n".join(found))
            return 1
        args.baseline.write_text(dump_baseline({k: min(v, per_file.get(k, 0)) for k, v in baseline.items()
                                                if per_file.get(k, 0) > 0}), encoding="utf-8")
        print(f"wrote {args.baseline.name}; {summary}")
        return 0
    found = problems + above + below
    if found:
        print("\n".join(found))
        print(f"\n{len(found)} test timing problem(s); see the docstring of scripts/check_test_timing.py")
        return 1
    print(f"test timing ratchet ok: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
