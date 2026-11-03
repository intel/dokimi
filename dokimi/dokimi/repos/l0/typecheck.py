# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Python fences of the guidance, type-checked against the repository's environment (contract §8.2).

Each fence that parses becomes a ``python_fence`` claim: ``ok`` when the tool reports no error,
``fail`` (a stale-API candidate) when it does, and ``unresolved`` when it does not parse or no
repository interpreter was given, so nothing was checked. One tool run covers every fence (mypy runs again
without the fences a blocking error named); the interpreter's site-packages are the pinned dependencies,
and imports are not followed past the fence.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi import process

from .core import Scan
from .markdown import PYTHON_LANGS, Fence, split

TOOLS = ("mypy", "basedpyright")
TIMEOUT = 600  # seconds for one run over every fence of a repository
MAX_WHY = 200
# The oldest repository Python each tool accepts: the pinned mypy 2.x refuses --python-version 3.9 and exits 2.
MIN_PYTHON = {"mypy": (3, 10)}
# Fragment-only errors (a name an earlier fence defines, await/return/yield outside a function), in mypy's or
# basedpyright's wording; any other error is real.
_FRAGMENT = re.compile(r'^(?:Name "[^"]+"|"[^"]+") is not defined(?: \[reportUndefinedVariable\])?$'
                       r'|^"(?:await|return|yield)" outside function$'
                       r'|^"await" allowed only within async function(?: \[\w+\])?$'
                       r'|^"return" can be used only within a function(?: \[\w+\])?$'
                       r'|^"yield" not allowed outside of a function or lambda(?: \[\w+\])?$')


class TypecheckError(RuntimeError):
    """The tool could not run or exited with a failure of its own (not a type error)."""


@dataclass(frozen=True)
class Check:
    tool: str  # ``mypy`` (dokimi's own interpreter, pinned in requirements-test.txt) or ``basedpyright`` (on PATH)
    python: str | None = None  # the repository's interpreter; None means unknown, nothing is checked


def python_fences(scan: Scan, files: list[str]) -> list[tuple[str, Fence, str]]:
    """Every fence tagged as Python in ``files``: (file, fence, dedented code)."""
    out = []
    for f in files:
        for fence in split(scan.text(f)).fences:
            if fence.lang in PYTHON_LANGS:
                out.append((f, fence, textwrap.dedent("\n".join(text for _, text in fence.lines))))
    return out


def check_fences(scan: Scan, files: list[str], check: Check) -> dict[str, Any]:
    """Add a ``python_fence`` claim per fence to ``scan``; the report's ``typecheck`` block."""
    if check.tool not in TOOLS:
        raise TypecheckError(f"unknown tool {check.tool!r}; one of {', '.join(TOOLS)}")
    found = python_fences(scan, files)
    block: dict[str, Any] = {"tool": check.tool, "tool_version": None, "python": check.python,
                             "fences": len(found), "checked": 0, "fail": 0, "unknown": 0, "syntax": 0}
    # The repository's grammar and version are the target: a fence is parsed by that interpreter, and checked as
    # that Python. No feature_version: it approximates an older grammar, the interpreter has the real one.
    target = python_version(check) if check.python is not None and found else None
    syntax = _syntax_errors(check, [code for _, _, code in found])
    runnable = []
    for (f, fence, code), why in zip(found, syntax, strict=True):
        if why is not None:  # `f(<arg>)` is not Python, or too deep for the parser
            block["syntax"] += 1
            scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                     f"not parsed by {check.python or 'dokimi'} ({why}): not type-checked")
            continue
        runnable.append((f, fence, code))
    if check.python is None:
        for f, fence, code in runnable:
            scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                     "unknown: no repository environment (--python-env), not type-checked")
        block["unknown"] = len(runnable)
        return block
    # Nothing to run, no tool run: a version probe would fail on a repository without the tool, for no result.
    block["tool_version"] = tool_version(check) if runnable else None
    if _older_than_the_tool(scan, check, runnable, target):
        block["unknown"] = len(runnable)
        return block
    found_errors = _run_past_blocking(check, [code for _, _, code in runnable], target) if runnable else {}
    fails, unchecked = _claims(scan, check.tool, runnable, found_errors)
    block["fail"] = fails
    block["checked"] = len(runnable) - unchecked
    block["unknown"] += unchecked  # "unknown" is every fence that was not checked
    return block


def _claims(scan: Scan, tool: str, runnable: list[tuple[str, Fence, str]],
            found_errors: dict[int, list[tuple[int, str]]]) -> tuple[int, int]:
    """One claim per runnable fence from the tool's errors; (failing fences, fences not checked)."""
    fails = unchecked = 0
    for i, (f, fence, code) in enumerate(runnable):
        errors = found_errors.get(i, [])
        if not errors:
            scan.add(f, fence.line, "python_fence", _label(code), "ok", f"{tool}: no error")
            continue
        if _is_fragment(errors):
            unchecked += 1
            scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                     f"{tool}: {errors[0][1]} (a fragment of a longer example: not type-checked)"[:MAX_WHY])
            continue
        fails += 1
        # the first real error names the claim: a fragment error of the same fence is only counted
        line, message = next(e for e in errors if not _FRAGMENT.match(e[1]))
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        where = f"line {line}: " if line else ""  # 0: the tool gave no line, so the claim is the fence itself
        scan.add(f, fence.line + line, "python_fence", _label(code), "fail",
                 f"{tool}: {where}{message}{more}"[:MAX_WHY])
    return fails, unchecked


def _older_than_the_tool(scan: Scan, check: Check, runnable: list[tuple[str, Fence, str]],
                         target: tuple[int, int] | None) -> bool:
    """True, with each runnable fence unresolved, when the repository is older than the tool checks (no run)."""
    floor = MIN_PYTHON.get(check.tool, (0, 0))
    if target is None or target >= floor:
        return False
    for f, fence, code in runnable:
        scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                 f"{check.tool} checks Python {floor[0]}.{floor[1]} and later; the repository is "
                 f"{target[0]}.{target[1]}: not type-checked")
    return True


def _is_fragment(errors: list[tuple[int, str]]) -> bool:
    """True when every error of a fence is one its missing context causes (see ``_FRAGMENT``)."""
    return all(_FRAGMENT.match(message) for _, message in errors)


def tool_version(check: Check) -> str:
    """The version the tool reports, e.g. ``2.4.0``. ``-I``: a module in the checkout cannot stand in for it."""
    argv = ([sys.executable, "-I", "-m", "mypy", "--version"] if check.tool == "mypy"
            else [check.tool, "--version"])
    done = _run_tool(argv, tempfile.gettempdir())
    if done.timed_out or done.returncode != 0:  # a missing module exits 1 with the reason on stderr
        raise TypecheckError(f"{check.tool} --version exited {done.returncode}: {done.stderr[-300:]}")
    parts = done.stdout.split()
    return parts[1] if len(parts) > 1 else done.stdout.strip()


def python_version(check: Check) -> tuple[int, int]:
    """The repository interpreter's (major, minor) version, read by the interpreter itself (``-I``)."""
    done = _run_tool([str(check.python), "-I", "-c", "import sys; print(*sys.version_info[:2])"],
                     tempfile.gettempdir())
    if done.timed_out or done.returncode != 0:
        raise TypecheckError(f"{check.python} did not report its version: {done.stderr[-300:]}")
    try:
        major, minor = done.stdout.split()[:2]
        return int(major), int(minor)
    except ValueError:  # no output, or output that is not two integers: the path is not a Python
        raise TypecheckError(f"{check.python} did not report its version: {done.stdout[-300:]!r}") from None


def _label(code: str) -> str:
    """The fence's first non-blank line, for the claim's text."""
    first = next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
    return first[:80]


_PARSE = """\
import ast, json, sys
def parse(code):
    try:
        ast.parse(code, type_comments=True)
    except TypeError:  # Python 3.7: ast.parse has no type_comments keyword (it came in 3.8)
        ast.parse(code)
out = []
for code in json.load(sys.stdin):
    try:
        parse(code)
        out.append(None)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        out.append((exc.msg or "invalid syntax") if isinstance(exc, SyntaxError) else type(exc).__name__)
json.dump(out, sys.stdout)
"""


def _syntax_errors(check: Check, codes: list[str]) -> list[str | None]:
    """Per fence: None when it parses, else why not. The repository's interpreter parses it (its own grammar);
    with none given, dokimi's own, ``-I`` and from the temporary directory, so the checkout cannot shadow it."""
    if not codes:
        return []
    python = check.python or sys.executable
    with tempfile.TemporaryDirectory(prefix="dokimi-typecheck-") as tmp:
        done = _run_tool([python, "-I", "-c", _PARSE], tmp, stdin=json.dumps(codes))
    if done.timed_out or done.returncode != 0 or done.truncated:
        raise TypecheckError(f"{python} did not parse the fences: {done.stderr[-300:]}")
    try:
        reasons = json.loads(done.stdout)
    except ValueError:
        reasons = None
    if not isinstance(reasons, list) or len(reasons) != len(codes):
        raise TypecheckError(f"{python} did not parse the fences: {done.stdout[-300:]!r}")
    return reasons


def _run_past_blocking(check: Check, codes: list[str],
                       target: tuple[int, int] | None) -> dict[int, list[tuple[int, str]]]:
    """Errors per fence index. A run mypy stopped on a blocking error is run again without the fences it named,
    so that one fence cannot leave every other fence unchecked; each round drops at least one fence."""
    errors: dict[int, list[tuple[int, str]]] = {}
    todo = list(range(len(codes)))
    while todo:
        found, blocked = _run(check, [codes[i] for i in todo], target)
        errors.update((todo[k], found_errors) for k, found_errors in found.items())
        if not blocked:
            break
        todo = [i for k, i in enumerate(todo) if k not in found]
    return errors


def _run(check: Check, codes: list[str],
         target: tuple[int, int] | None = None) -> tuple[dict[int, list[tuple[int, str]]], bool]:
    """Errors per fence index: (line in the fence, message), in tool order; and whether the tool stopped early.
    ``target``: the repository's version."""
    with tempfile.TemporaryDirectory(prefix="dokimi-typecheck-") as tmp:
        names = []
        for i, code in enumerate(codes):
            # A hyphen makes the name unimportable: no ``import`` in one fence can resolve to another fence's file,
            # so every fence is checked against the repository's environment alone.
            Path(tmp, f"fence-{i}.py").write_text(code + "\n", encoding="utf-8")
            names.append(f"fence-{i}.py")
        if check.tool == "mypy":
            # The config is explicit and empty: no subject or host mypy settings apply. -I: nothing in the
            # run directory or PYTHON* variables can stand in for mypy or for the standard library.
            config = os.path.join(tmp, "mypy.ini")
            Path(config).write_text("[mypy]\n", encoding="utf-8")
            version = [] if target is None else ["--python-version", f"{target[0]}.{target[1]}"]
            argv = [sys.executable, "-I", "-m", "mypy", "--python-executable", str(check.python), *version,
                    "--config-file", config, "--check-untyped-defs",
                    "--follow-imports=silent", "--no-incremental", "--cache-dir", os.path.join(tmp, "cache"),
                    "--output", "json", *names]
        else:
            # The project is explicit and empty: a pyrightconfig.json above the temporary directory is not read.
            # the target version is set here: --pythonpath gives the import paths, not the version checked against
            settings = {} if target is None else {"pythonVersion": f"{target[0]}.{target[1]}"}
            project = os.path.join(tmp, "pyrightconfig.json")
            Path(project).write_text(json.dumps(settings) + "\n", encoding="utf-8")
            argv = [check.tool, "--project", project, "--pythonpath", str(check.python), "--outputjson", *names]
        done = _run_tool(argv, tmp)
    # 1: the tool found errors. mypy's 2: it stopped on a blocking one ("errors prevented further checking"),
    # which is a result only when it names a fence. Any other exit is the tool's own failure (pyright's 2 is fatal).
    blocked = check.tool == "mypy" and done.returncode == 2
    if done.timed_out or (done.returncode not in (0, 1) and not blocked):
        raise TypecheckError(f"{check.tool} exited {done.returncode}: {done.stderr[-300:]}")
    if done.truncated:  # the middle of the output was dropped, so an error there would be missed
        raise TypecheckError(f"{check.tool} output was truncated; not every error is known")
    if done.returncode == 1 and not done.stdout.strip():  # exit 1 with no report: the tool did not run
        raise TypecheckError(f"{check.tool} exited 1 with no report: {done.stderr[-300:]}")
    errors = _errors(check.tool, done.stdout)
    if blocked:
        if not errors:  # a usage error, or a blocking one that names no fence
            raise TypecheckError(f"{check.tool} exited 2 with no fence report: {done.stderr[-300:]}")
        return errors, True
    return errors, False


# What a tool run may inherit from the host. Not the whole environment: MYPYPATH, for one, adds modules to
# mypy's search path, so a fence could pass on a module the pinned environment does not have.
_TOOL_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")


def _run_tool(argv: list[str], cwd: str, stdin: str | None = None):
    try:
        return process.run(argv, timeout=TIMEOUT, env=process.Env.only(_TOOL_ENV), cwd=cwd, input=stdin)
    except OSError as exc:
        raise TypecheckError(f"cannot run {argv[0]}: {exc}") from exc


def _errors(tool: str, stdout: str) -> dict[int, list[tuple[int, str]]]:
    """Parse the tool's JSON output into errors keyed by fence index."""
    out: dict[int, list[tuple[int, str]]] = {}
    # A report we cannot read is a tool failure, not a clean run: the fences it would have flagged pass otherwise.
    diagnostics = _mypy_errors(stdout) if tool == "mypy" else _basedpyright_errors(stdout)
    for name, line, message in diagnostics:
        index = _fence_index(name)
        if index is not None:
            out.setdefault(index, []).append((line, message))
    return out


def _mypy_errors(stdout: str) -> list[tuple[str, int, str]]:
    """The error diagnostics in mypy's report: one JSON object per line."""
    diagnostics = []
    for text in stdout.splitlines():
        if not text.strip():
            continue
        try:
            d = json.loads(text)
            # A valid JSON object is not a diagnostic: without these fields "{}" would read as a clean run.
            if not isinstance(d, dict) or not {"file", "line", "severity", "message"} <= d.keys():
                raise ValueError("not a diagnostic")
            if d["severity"] == "error":
                line = int(d.get("line") or 0)  # mypy writes -1 for an error with no line
                diagnostics.append((str(d.get("file")), max(line, 0), str(d.get("message"))))
        except (ValueError, TypeError) as exc:
            raise TypecheckError(f"mypy wrote a line that is not a diagnostic in its report: {text[:200]!r}") \
                from exc
    return diagnostics


def _basedpyright_errors(stdout: str) -> list[tuple[str, int, str]]:
    """The error diagnostics in basedpyright's report: one document whose ranges are zero-based."""
    try:
        doc = json.loads(stdout)  # empty output is not a report either
        if not isinstance(doc, dict) or not isinstance(doc.get("generalDiagnostics"), list):
            raise ValueError("no generalDiagnostics list")  # a missing list is not an empty one
        return [(Path(str(d.get("file"))).name, int(d["range"]["start"]["line"]) + 1,
                 str(d.get("message")) + (f" [{d['rule']}]" if d.get("rule") else ""))
                for d in doc["generalDiagnostics"] if d.get("severity") == "error"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise TypecheckError(f"basedpyright wrote a report that is not in its JSON format: {exc}") from exc


def _fence_index(name: str) -> int | None:
    """``3`` for ``fence-3.py``; None for any other file (a note about an import, say)."""
    stem = Path(name).name
    if stem.startswith("fence-") and stem.endswith(".py") and stem[6:-3].isdigit():
        return int(stem[6:-3])
    return None
