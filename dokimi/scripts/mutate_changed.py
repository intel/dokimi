#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Mutation-test only the lines a PR changes in the ``dokimi`` package.

The by-hand step in ``AGENTS.md`` ("break the code it covers, confirm the test
fails"), run by CI: a surviving mutant is a changed line that no test notices
breaking. Steps:

1. Changed lines: ``git diff -U0`` of the working tree against the merge base of BASE and HEAD, over ``dokimi/dokimi/**.py``.
2. Candidate tests: the files under ``tests/`` that import a changed module.
3. Covering tests: one coverage run of those files with per-test contexts
   (``--cov-context=test``), one file at a time for at most half of ``--budget``,
   maps each changed line to the tests that execute it.
4. Mutants: a small AST mutator on the changed lines only (flip a comparison,
   swap ``and``/``or``, negate an ``if``/``while`` condition, return ``None``,
   flip a boolean constant, bump an integer, swap ``+``/``-``, drop ``not``),
   spliced into the source text so the rest of the file is untouched.
   At most ``--max-mutants``, spread round-robin over the changed lines.
5. Each mutant runs the tests covering its line, ``-x``, in a copy of the
   tracked tree (subprocess tests see the mutant too; the checkout is never
   written). Exit 0 from pytest is a survivor, exit 1 (a test failed) a kill, and
   so is a collection error the mutant caused (``_mutant_status``); a timeout and
   any other exit code (no test ran, a usage error) get their own status, never a kill.
   A survivor whose module has a candidate test file that did not run (out of time,
   or failing to collect) is ``inconclusive``: that file may be the one that catches
   the change, so it is not reported as a gap.

A line no candidate test executes, or one that continues a multi-line expression
or signature, is listed as not mutated: the changed-lines coverage gate in ``ci.yml`` owns the first. The run stops starting
mutants after ``--budget`` seconds and reports the rest as not run.

    python scripts/mutate_changed.py --base origin/main [--max-mutants 40]
        [--json out.json] [--markdown out.md] [--fail-on-survivor]

Tests that fail without a mutant are left out and listed. Exits 0, or 1 with
``--fail-on-survivor`` when a mutant survives; git failing to diff ``--base``
exits with its message.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from collections.abc import Iterator
from io import StringIO
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parent
if str(PROJECT) not in sys.path:  # run as a script from CI, not installed
    sys.path.insert(0, str(PROJECT))

from dokimi import process  # noqa: E402
PKG = "dokimi/dokimi/"
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_CMP_SWAP: dict[type, type] = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}


@dataclass
class Mutant:
    path: str  # repo-relative
    line: int
    op: str
    start: tuple[int, int]
    end: tuple[int, int]
    replacement: str
    original: str
    status: str = "not run"
    tests: list[str] = field(default_factory=list)
    seconds: float = 0.0


def changed_lines(base: str) -> dict[str, set[int]]:
    """Added or edited line numbers per changed package file, the working tree against the merge base of ``base``.

    Committed and uncommitted edits of tracked files count, as the mutants are cut from the working tree
    (untracked files do not: CI checks out a clean tree).
    """
    mb = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    if mb.returncode:
        raise SystemExit(f"git merge-base {base} HEAD failed: {mb.stderr.strip()}")
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "-U0", "--no-color", "--diff-filter=AMR", mb.stdout.strip(), "--", f"{PKG}*.py"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode:
        raise SystemExit(f"git diff {mb.stdout.strip()} failed: {out.stderr.strip()}")
    result: dict[str, set[int]] = {}
    path = None
    for line in out.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif path and (m := HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            result.setdefault(path, set()).update(range(start, start + count))
    return {p: s for p, s in result.items() if s}


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _span(node: ast.AST) -> tuple[tuple[int, int], tuple[int, int]]:
    return (node.lineno, node.col_offset), (node.end_lineno or node.lineno, node.end_col_offset or 0)  # type: ignore[attr-defined]


def _edits(node: ast.AST, src: str) -> Iterator[tuple[str, ast.AST, str]]:
    """(operator name, node whose source span is replaced, replacement text)."""
    yield from _logic_edits(node, src)
    yield from _value_edits(node)


def _logic_edits(node: ast.AST, src: str) -> Iterator[tuple[str, ast.AST, str]]:
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAP:
        new = ast.Compare(left=node.left, ops=[_CMP_SWAP[type(node.ops[0])]()], comparators=node.comparators)
        yield "flip-compare", node, ast.unparse(new)
    elif isinstance(node, ast.BoolOp):
        new_op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        yield "swap-boolop", node, ast.unparse(ast.BoolOp(op=new_op, values=node.values))
    elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
        seg = ast.get_source_segment(src, node.test)
        if seg is not None:
            yield "negate-condition", node.test, f"not ({seg})"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        seg = ast.get_source_segment(src, node.operand)
        if seg is not None:
            yield "drop-not", node, f"({seg})"


def _value_edits(node: ast.AST) -> Iterator[tuple[str, ast.AST, str]]:
    if isinstance(node, ast.Return) and node.value is not None:
        if not (isinstance(node.value, ast.Constant) and node.value.value is None):
            yield "return-none", node.value, "None"
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        yield "flip-bool", node, repr(not node.value)
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        yield "bump-int", node, repr(node.value + 1)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        if not any(isinstance(s, ast.Constant) and isinstance(s.value, str) for s in (node.left, node.right)):
            new_bin = ast.BinOp(left=node.left, op=ast.Sub() if isinstance(node.op, ast.Add) else ast.Add(), right=node.right)
            yield "swap-addsub", node, ast.unparse(new_bin)


def mutants_for(path: str, src: str, lines: set[int]) -> list[Mutant]:
    """Every mutant whose node starts on a changed line, in source order."""
    tree = ast.parse(src)
    skip = _docstring_lines(tree)
    out: list[Mutant] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", None)
        if line is None or line not in lines or line in skip:
            continue
        for op, target, replacement in _edits(node, src):
            original = ast.get_source_segment(src, target)
            if original is None or original == replacement:
                continue
            start, end = _span(target)
            out.append(Mutant(path, line, op, start, end, replacement, original))
    out.sort(key=lambda m: (m.line, m.start, m.op))
    return out


def _text_lines(src: str) -> set[int]:
    """Lines strictly inside a multi-line string token: pure text, no code (a string's first and last lines may hold code)."""
    kinds = {tokenize.STRING} | ({tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())
    out: set[int] = set()
    for tok in tokenize.generate_tokens(StringIO(src).readline):
        if tok.type in kinds and tok.end[0] > tok.start[0] + 1:
            out.update(range(tok.start[0] + 1, tok.end[0]))
    return out


def continuation_lines(src: str, lines: set[int], mutated: set[int]) -> set[int]:
    """Changed lines inside a multi-line expression or def signature that no mutant starts on, as ``+ b)`` in ``x = (a``.

    Docstrings, comments, blank lines and the text of multi-line strings are not listed: they hold no code a test could run.
    """
    tree = ast.parse(src)
    skip = _docstring_lines(tree) | _text_lines(src)
    spanned: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.expr):
            spanned.update(range(node.lineno + 1, (node.end_lineno or node.lineno) + 1))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spanned.update(range(node.lineno + 1, node.body[0].lineno))
    text = re.split(r"(?<=\n)", src)
    return {n for n in lines if n in spanned and n not in mutated and n not in skip
            and text[n - 1].strip() and not text[n - 1].lstrip().startswith("#")}


def apply(src: str, m: Mutant) -> str:
    """``src`` with the mutant's span replaced; columns are UTF-8 byte offsets."""
    # Split after each \n only: splitlines also breaks on other Unicode line breaks, which ast does not count.
    lines = re.split(r"(?<=\n)", src)
    (sl, sc), (el, ec) = m.start, m.end
    head = lines[sl - 1].encode("utf-8")[:sc].decode("utf-8")
    tail = lines[el - 1].encode("utf-8")[ec:].decode("utf-8")
    return "".join(lines[: sl - 1]) + head + m.replacement + tail + "".join(lines[el:])


def pick(mutants: list[Mutant], cap: int) -> list[Mutant]:
    """Round-robin over (file, line) so a long changed function does not take the whole cap."""
    by_line: dict[tuple[str, int], list[Mutant]] = {}
    for m in mutants:
        by_line.setdefault((m.path, m.line), []).append(m)
    queues = list(by_line.values())
    chosen: list[Mutant] = []
    while queues and len(chosen) < cap:
        for q in list(queues):
            if len(chosen) >= cap:
                break
            chosen.append(q.pop(0))
            if not q:
                queues.remove(q)
    return chosen


def candidate_tests(paths: list[str], tests_dir: Path) -> list[str]:
    """Test files that import a changed module (``dokimi.a.b`` or ``from dokimi.a import b``).

    Files named after a changed module (``test_b*.py``) come first: the coverage pass
    runs them in this order and stops at its share of the budget.
    """
    specs, leaves = [], []
    for p in paths:
        dotted = p[len("dokimi/"):-len(".py")].replace("/", ".").removesuffix(".__init__")
        parent, _, leaf = dotted.rpartition(".")
        leaves.append(f"test_{leaf}")
        specs.append((re.compile(rf"\b{re.escape(dotted)}\b"), parent, leaf))
    found = []
    for f in sorted(tests_dir.glob("test_*.py")):
        text = f.read_text(encoding="utf-8", errors="replace")
        froms = _from_imports(text)
        if any(p.search(text) or (parent, leaf) in froms for p, parent, leaf in specs):
            found.append(f"tests/{f.name}")
    return sorted(found, key=lambda t: not any(Path(t).stem.startswith(leaf) for leaf in leaves))


def _from_imports(text: str) -> set[tuple[str, str]]:
    """(module, name) of each ``from module import name``, however the statement is wrapped; none if it does not parse."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    return {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module for a in n.names}


def _write_source(target: Path, text: str) -> None:
    # A same-size rewrite in the same second would otherwise run the cached bytecode of the previous text.
    target.write_text(text, encoding="utf-8")
    for pyc in (target.parent / "__pycache__").glob(f"{target.stem}.*.pyc"):
        pyc.unlink()


def copy_tree(dest: Path) -> None:
    files = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout.decode("utf-8")
    for rel in filter(None, files.split("\0")):
        src = REPO / rel
        if src.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def _pytest(
    cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None
) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "console_output_style=classic", *args]
    # process.run kills the whole process tree on exit or timeout: no test's child outlives its mutant.
    # capture_limit=None: the coverage pass parses every FAILED/PASSED line, a truncated summary would lose some.
    done = process.run(cmd, timeout=timeout, env=process.Env.inherit(env), cwd=cwd, capture_limit=None)
    if done.timed_out:
        return -1, "timeout"
    return done.returncode, done.stdout + done.stderr


def _failed(log: str, ids: set[str]) -> tuple[set[str], list[str]]:
    """The ids in ``ids`` that pytest's summary names as failed, and the summary's other names.

    A summary line is ``FAILED <node id> - <message>``, and a node id can hold `` - `` (a parameter), so a line is
    attributed to the longest known id it starts with (``f[a]`` is a prefix of the line of ``f[a] - b]``), among
    the ids with no ``PASSED`` line of their own when there are any (a message can complete a longer id: ``f[a] - Failed: b]``);
    only a name that matches none is cut at the first `` - ``.
    """
    named: set[str] = set()
    unmatched: list[str] = []
    passed = set(re.findall(r"^PASSED (.+)$", log, re.MULTILINE))
    for line in re.findall(r"^(?:FAILED|ERROR) (.+)$", log, re.MULTILINE):
        found = [i for i in ids if line == i or line.startswith(i + " - ")]
        match = max([i for i in found if i not in passed] or found, key=len, default=None)
        if match is None:
            unmatched.append(line.split(" - ", 1)[0])
        else:
            named.add(match)
    return named, unmatched


_PASSED = re.compile(r"\b[1-9]\d* passed\b")  # the final pytest line of a run in which a test ran
_COLLECTION_ERROR = "ERROR collecting "  # a module that does not import: the mutant broke it (pytest's exit code for this varies)


def _mutant_status(rc: int, log: str) -> str:
    """1 is a failed test. A collection error (exit 2 or 4 by pytest version) is one too: the file collected unmutated,
    so the mutant broke the import. Other exits (internal, usage, nothing collected) say nothing about the mutant."""
    if rc == 0:
        return "survived"
    if rc == -1:
        return "timeout"
    if rc == 1 or (rc in (2, 4) and _COLLECTION_ERROR in log):
        return "killed"
    return "error"


def covering_tests(
    work: Path, tests: list[str], paths: list[str], budget: float
) -> tuple[dict[str, dict[int, list[str]]], list[str], list[str]]:
    """Run each test file with per-test coverage contexts, in order, until ``budget`` seconds pass.

    Returns line -> passing test node ids per changed file, what was left out because
    it fails unmutated (a node id, or a file that does not collect: it would kill every
    mutant), and the files not run for lack of time. One process per file, not xdist:
    pytest-cov's per-test contexts break under xdist workers.
    """
    import coverage

    t0 = time.monotonic()
    by_file: dict[str, dict[int, list[str]]] = {}
    failing: list[str] = []
    data_file = work / "dokimi" / ".coverage"
    for i, test in enumerate(tests):
        left = budget - (time.monotonic() - t0)
        if left <= 0:
            return by_file, failing, tests[i:]
        data_file.unlink(missing_ok=True)
        rc, log = _pytest(
            work / "dokimi", ["-rfEp", "--cov=dokimi", "--cov-context=test", "--cov-report=", test], left,
            # -rp lists PASSED ids too (without the captured output of passing tests): a test that reads an import-time value has no context on the file's lines.
            # sys.monitoring (the default core on 3.14+) records no per-test contexts.
            env={"COVERAGE_CORE": "ctrace"},
        )
        if rc == -1:
            return by_file, failing, tests[i:]
        if rc not in (0, 1, 5) or not data_file.exists():
            failing.append(test)
            continue
        ran = _PASSED.search(log) is not None
        data = coverage.CoverageData(basename=str(data_file))
        data.read()
        measured = {Path(f).resolve(): f for f in data.measured_files()}
        per_file = {rel: data.contexts_by_lineno(measured[(work / rel).resolve()])
                    for rel in paths if (work / rel).resolve() in measured}
        executed = {c.rsplit("|", 1)[0] for ctxs in per_file.values() for cs in ctxs.values() for c in cs if c}
        passed = set(re.findall(r"^PASSED (.+)$", log, re.MULTILINE))
        # a passing test that errors in teardown is both PASSED and ERROR: its ERROR makes it bad
        bad, unmatched = _failed(log, executed | passed) if rc == 1 else (set(), [])
        failing += sorted(bad) + unmatched
        for rel, ctxs in per_file.items():
            lines = by_file.setdefault(rel, {})
            for ln, cs in ctxs.items():
                ids = {c.rsplit("|", 1)[0] for c in cs if c}
                if "" in cs and ran:  # run at collection (an import-time constant or default): every test of the file runs it
                    # with no failure, the file's tests; with one, the tests of the file that passed
                    ids |= {test} if not (bad or unmatched) else passed - bad
                if ids := ids - bad:
                    lines[ln] = sorted(set(lines.get(ln, [])) | ids)
    return by_file, failing, []


def _not_run(failing: list[str], skipped: list[str]) -> list[str]:
    """Candidate test files that did not run: left for lack of time, or failing to collect (a bare file in ``failing``)."""
    return skipped + [f for f in failing if "::" not in f]


def _collect(changed: dict[str, set[int]], report: dict) -> list[Mutant]:
    all_mutants: list[Mutant] = []
    for path, lines in sorted(changed.items()):
        try:
            src = (REPO / path).read_text(encoding="utf-8")
            ms = mutants_for(path, src, lines)
        except (SyntaxError, UnicodeDecodeError):
            continue
        report["files"][path] = {"changed_lines": len(lines), "mutants_possible": len(ms)}
        all_mutants += ms
        conts = continuation_lines(src, lines, {m.line for m in ms})
        if conts:
            report["uncovered_lines"].setdefault(path, set()).update(conts)
    return all_mutants


def run(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    report: dict = {"base": args.base, "max_mutants": args.max_mutants, "files": {}, "mutants": [], "uncovered_lines": {}}
    all_mutants = _collect(changed_lines(args.base), report)
    if not all_mutants:
        report["note"] = "no mutable changed lines in the dokimi package"
        return _finish(report, args, t0, 0)

    tests = candidate_tests(sorted({m.path for m in all_mutants}), PROJECT / "tests")
    report["candidate_tests"] = tests
    with tempfile.TemporaryDirectory(prefix="dokimi-mut-") as tmp:
        work = Path(tmp)
        copy_tree(work)
        cover: dict[str, dict[int, list[str]]] = {}
        failing: list[str] = []
        skipped: list[str] = []
        if tests:
            cover, failing, skipped = covering_tests(
                work, tests, sorted({m.path for m in all_mutants}), args.budget / 2
            )
            report["failing_unmutated"] = failing
            report["candidate_tests_not_run"] = skipped
        gaps = _not_run(failing, skipped)
        unrun = {p: [t for t in candidate_tests([p], PROJECT / "tests") if t in gaps]
                 for p in {m.path for m in all_mutants}} if gaps else {}
        covered = [m for m in all_mutants if cover.get(m.path, {}).get(m.line)]
        for m in all_mutants:
            if not cover.get(m.path, {}).get(m.line):
                report["uncovered_lines"].setdefault(m.path, set()).add(m.line)
        chosen = pick(covered, args.max_mutants)
        report["mutants_covered"] = len(covered)
        for m in chosen:
            if time.monotonic() - t0 > args.budget:
                break
            m.tests = cover[m.path][m.line]
            # Every covering test, read by pytest from a file: a widely covered line can outgrow a command line.
            tests_file = work / "mutant-tests.txt"
            tests_file.write_text("\n".join(m.tests), encoding="utf-8")
            target = work / m.path
            original = target.read_text(encoding="utf-8")
            _write_source(target, apply(original, m))
            start = time.monotonic()
            try:
                rc, log = _pytest(work / "dokimi", ["-x", "-p", "no:xdist", f"@{tests_file}"], args.mutant_timeout)
            finally:
                _write_source(target, original)
            m.seconds = round(time.monotonic() - start, 1)
            m.status = _mutant_status(rc, log)
            if m.status == "survived" and unrun.get(m.path):
                m.status = "inconclusive"
        report["mutants"] = [asdict(m) for m in chosen]
    report["uncovered_lines"] = {p: sorted(s) for p, s in report["uncovered_lines"].items()}
    survived = sum(m.status == "survived" for m in chosen)
    return _finish(report, args, t0, 1 if survived and args.fail_on_survivor else 0)


def markdown(report: dict) -> str:
    ms = report["mutants"]
    count = {s: sum(m["status"] == s for m in ms) for s in ("killed", "timeout", "error", "survived", "inconclusive", "not run")}
    out = ["## Mutation of changed lines\n"]
    if note := report.get("note"):
        out.append(f"{note}.\n")
    out.append(
        f"{len(ms)} mutants of {report.get('mutants_covered', 0)} on covered changed lines (cap {report['max_mutants']}): "
        f"{count['killed']} killed, {count['timeout']} timed out, {count['error']} errors, "
        f"**{count['survived']} survived**, {count['inconclusive']} inconclusive, {count['not run']} not run (time budget). {report['seconds']} s.\n"
    )
    survivors = [m for m in ms if m["status"] == "survived"]
    if survivors:
        out.append("A survivor is a change to that line that the tests covering it do not notice; "
                   "add an assertion that fails on it, or say in the PR why it is equivalent.\n")
        out.append("| Line | Operator | Was | Became | Tests run |\n|---|---|---|---|---:|")
        for m in survivors:
            was, became = (s.replace("|", "\\|").replace("\n", " ")[:80] for s in (m["original"], m["replacement"]))
            out.append(f"| `{m['path']}:{m['line']}` | {m['op']} | `{was}` | `{became}` | {len(m['tests'])} |")
        out.append("")
    if count["inconclusive"]:
        out.append(f"{count['inconclusive']} inconclusive: the tests covering the line pass, but a candidate test file "
                   "for its module did not run (listed below), so it may be the one that catches the change. Not a gap.\n")
    if report["uncovered_lines"]:
        lines = "; ".join(f"`{p}`: {', '.join(map(str, ls))}" for p, ls in report["uncovered_lines"].items())
        out.append("Changed lines not mutated (no candidate test executes them, "
                   f"or they continue a multi-line expression or signature): {lines}\n")
    if skipped := report.get("candidate_tests_not_run"):
        out.append(f"{len(skipped)} candidate test files not run (coverage pass out of time): "
                   + ", ".join(f"`{t}`" for t in skipped[:20]) + ("…" if len(skipped) > 20 else "") + "\n")
    if failing := report.get("failing_unmutated"):
        out.append(f"{len(failing)} candidate tests fail without a mutant and were left out: "
                   + ", ".join(f"`{t}`" for t in failing[:20]) + ("…" if len(failing) > 20 else "") + "\n")
    return "\n".join(out)


def _finish(report: dict, args: argparse.Namespace, t0: float, code: int) -> int:
    report.setdefault("mutants", [])
    report["uncovered_lines"] = {p: sorted(s) for p, s in report.get("uncovered_lines", {}).items()}
    report["seconds"] = round(time.monotonic() - t0, 1)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    text = markdown(report)
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    print(text)
    for m in report["mutants"]:
        if m["status"] == "survived":
            level = "error" if args.fail_on_survivor else "warning"
            print(f"::{level} file={m['path']},line={m['line']}::mutant survived: {m['op']} `{m['original']}` -> `{m['replacement']}`")
        elif m["status"] == "inconclusive":
            print(f"::warning file={m['path']},line={m['line']}::mutant inconclusive: a candidate test file for it did not run")
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="git ref the PR is diffed against (merge base of BASE...HEAD)")
    ap.add_argument("--max-mutants", type=int, default=40)
    ap.add_argument("--mutant-timeout", type=float, default=180.0, help="seconds per mutant; a mutant that times out is reported as timed out, neither killed nor survived")
    ap.add_argument("--budget", type=float, default=1200.0, help="seconds after which no new mutant starts")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    ap.add_argument("--fail-on-survivor", action="store_true")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    process.install_signal_handlers()
    sys.exit(main())
