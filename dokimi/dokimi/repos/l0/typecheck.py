# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Python fences of the guidance, type-checked against the repository's environment (contract §8.2).

Each fence that parses becomes a ``python_fence`` claim: ``ok`` when the tool reports no error,
``fail`` (a stale-API candidate) when it does, and ``unresolved`` when it does not parse or no
repository interpreter was given, so nothing was checked. One tool run covers every fence; the
interpreter's site-packages are the pinned dependencies, and imports are not followed past the fence.
"""

from __future__ import annotations

import ast
import json
import os
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


class TypecheckError(RuntimeError):
    """The tool could not run or exited with a failure of its own (not a type error)."""


@dataclass(frozen=True)
class Check:
    tool: str  # ``mypy`` (the host's pinned copy) or ``basedpyright`` (on PATH)
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
    # The repository's grammar and version are the target: a fence is parsed as that Python parses it.
    target = python_version(check) if check.python is not None and found else None
    runnable = []
    for f, fence, code in found:
        try:
            ast.parse(code, feature_version=target)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:  # `f(<arg>)` is not Python
            block["syntax"] += 1
            why = exc.msg if isinstance(exc, SyntaxError) else type(exc).__name__  # too deep: no parse tree
            scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                     f"not valid Python ({why}): not type-checked")
            continue
        runnable.append((f, fence, code))
    if check.python is None:
        for f, fence, code in runnable:
            scan.add(f, fence.line, "python_fence", _label(code), "unresolved",
                     "unknown: no repository environment (--python-env), not type-checked")
        block["unknown"] = len(runnable)
        return block
    block["tool_version"] = tool_version(check)
    found_errors = _run(check, [code for _, _, code in runnable], target) if runnable else {}  # no file: usage error
    for i, (f, fence, code) in enumerate(runnable):
        errors = found_errors.get(i, [])
        if not errors:
            scan.add(f, fence.line, "python_fence", _label(code), "ok", f"{check.tool}: no error")
            continue
        block["fail"] += 1
        line, message = errors[0]
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        where = f"line {line}: " if line else ""  # 0: the tool gave no line, so the claim is the fence itself
        scan.add(f, fence.line + line, "python_fence", _label(code), "fail",
                 f"{check.tool}: {where}{message}{more}"[:MAX_WHY])
    block["checked"] = len(runnable)
    return block


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


def _run(check: Check, codes: list[str], target: tuple[int, int] | None = None) -> dict[int, list[tuple[int, str]]]:
    """Errors per fence index: (line in the fence, message), in tool order. ``target``: the repository's version."""
    with tempfile.TemporaryDirectory(prefix="dokimi-typecheck-") as tmp:
        names = []
        for i, code in enumerate(codes):
            Path(tmp, f"fence_{i}.py").write_text(code + "\n", encoding="utf-8")
            names.append(f"fence_{i}.py")
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
            argv = [check.tool, "--pythonpath", str(check.python), "--outputjson", *names]
        done = _run_tool(argv, tmp)
    if done.timed_out or done.returncode not in (0, 1):  # 1: the tool found errors; anything else is its failure
        raise TypecheckError(f"{check.tool} exited {done.returncode}: {done.stderr[-300:]}")
    if done.truncated:  # the middle of the output was dropped, so an error there would be missed
        raise TypecheckError(f"{check.tool} output was truncated; not every error is known")
    if done.returncode == 1 and not done.stdout.strip():  # exit 1 with no report: the tool did not run
        raise TypecheckError(f"{check.tool} exited 1 with no report: {done.stderr[-300:]}")
    return _errors(check.tool, done.stdout)


# What a tool run may inherit from the host. Not the whole environment: MYPYPATH, for one, adds modules to
# mypy's search path, so a fence could pass on a module the pinned environment does not have.
_TOOL_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT")


def _run_tool(argv: list[str], cwd: str):
    try:
        return process.run(argv, timeout=TIMEOUT, env=process.Env.only(_TOOL_ENV), cwd=cwd)
    except OSError as exc:
        raise TypecheckError(f"cannot run {argv[0]}: {exc}") from exc


def _errors(tool: str, stdout: str) -> dict[int, list[tuple[int, str]]]:
    """Parse the tool's JSON output into errors keyed by fence index."""
    out: dict[int, list[tuple[int, str]]] = {}
    if tool == "mypy":  # one JSON object per line
        diagnostics = []
        for text in stdout.splitlines():
            try:
                d = json.loads(text)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("severity") == "error":
                line = int(d.get("line") or 0)  # mypy writes -1 for an error with no line
                diagnostics.append((str(d.get("file")), max(line, 0), str(d.get("message"))))
    else:  # basedpyright: one document; its ranges are zero-based
        doc = json.loads(stdout or "{}")
        diagnostics = [(Path(str(d.get("file"))).name, int(d["range"]["start"]["line"]) + 1,
                        str(d.get("message")) + (f" [{d['rule']}]" if d.get("rule") else ""))
                       for d in doc.get("generalDiagnostics", []) if d.get("severity") == "error"]
    for name, line, message in diagnostics:
        index = _fence_index(name)
        if index is not None:
            out.setdefault(index, []).append((line, message))
    return out


def _fence_index(name: str) -> int | None:
    """``3`` for ``fence_3.py``; None for any other file (a note about an import, say)."""
    stem = Path(name).name
    if stem.startswith("fence_") and stem.endswith(".py") and stem[6:-3].isdigit():
        return int(stem[6:-3])
    return None
