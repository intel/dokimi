# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``dokimi:typecheck``: the listed files type-check clean under the image's pinned mypy (contract §4.5).

task.json: ``"tool": "mypy"``, ``"version"`` (exactly the image's mypy version, else a grader
failure) and ``"paths"`` (workspace-relative files). ``pass`` is true when no error is reported
on a listed file and every listed file exists. Errors in other modules are not counted.

It runs mypy in-process: the grader cannot start a child (the import layers allow spawns only in
``dokimi.process``, which the container cannot import), so basedpyright is not offered here. The
host-side L0 check (``repo scan --typecheck``) runs both tools. Standard library only at import
time; mypy is imported by :func:`grade` and must be importable by the image's ``python3``.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

# Bump when a result for the same inputs changes (a metric or ``pass``).
VERSION = "1"
TOOL = "mypy"
MAX_ERRORS = 50  # reported in details; the count is in the metrics
_ERROR = re.compile(r"^(?P<file>.+?)(?::(?P<line>\d+))?(?::\d+)?: error: (?P<message>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$")


def _safe_path(path: object) -> bool:
    """A workspace-relative path: no leading slash, no ``..`` segment, not empty."""
    return isinstance(path, str) and bool(path) and not path.startswith("/") and ".." not in path.split("/")


def task_problems(raw: dict[str, Any]) -> list[str]:
    """Problems with a ``dokimi:typecheck`` task's own fields (checked by ``tasks check``)."""
    problems = []
    if raw.get("tool", TOOL) != TOOL:
        problems.append(f"tool must be {TOOL!r}: basedpyright starts a process, which the grader does not "
                        f"(the L0 check runs it), not {raw.get('tool')!r}")
    if not isinstance(raw.get("version"), str) or not raw.get("version"):
        problems.append('version must be the pinned mypy version, e.g. "2.4.0"')
    paths = raw.get("paths")
    if not isinstance(paths, list) or not paths or not all(_safe_path(p) for p in paths):
        problems.append("paths must be a non-empty list of workspace-relative file paths")
    return problems


def _errors(stdout: str, listed: set[str], root: str) -> list[dict[str, Any]]:
    """The error lines of mypy's output on a listed file, in output order; ``root`` is stripped from the names."""
    out = []
    for line in stdout.splitlines():
        m = _ERROR.match(line)
        if not m:
            continue
        name = m.group("file")
        name = posixpath.normpath(name[len(root):] if name.startswith(root) else name)
        if name in listed:
            out.append({"file": name, "line": int(m.group("line") or 0),
                        "code": m.group("code") or "", "message": m.group("message")})
    return out


def grade(task: dict[str, Any], task_dir: Path, answer: str, run_dir: Path | None = None) -> dict[str, Any]:
    """Type-check the listed files in the workspace of ``run_dir``; the grader's result dict."""
    del task_dir, answer  # the answer is not read: the workspace is the subject
    problems = task_problems(task)
    if problems:
        raise ValueError("; ".join(problems))
    import mypy.version
    from mypy import api

    if mypy.version.__version__ != task["version"]:
        raise ValueError(f"the image's mypy is {mypy.version.__version__}, the task pins {task['version']}")
    workspace = Path(run_dir or "/eval/run") / "workspace"
    paths = [posixpath.normpath(p) for p in task["paths"]]
    missing = [p for p in paths if not (workspace / p).is_file()]
    present = [p for p in paths if p not in missing]
    errors: list[dict[str, Any]] = []
    if present:
        cache = tempfile.mkdtemp(prefix="dokimi-mypy-")
        neutral = tempfile.mkdtemp(prefix="dokimi-mypy-cwd-")  # no config here: the subject's mypy.ini is not read
        cwd = os.getcwd()
        try:
            os.chdir(neutral)
            stdout, stderr, status = api.run(["--no-incremental", f"--cache-dir={cache}",
                                              *[str(workspace / p) for p in present]])
        finally:
            os.chdir(cwd)
            shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(neutral, ignore_errors=True)
        if status not in (0, 1):  # 2: mypy itself failed (a bad config, a crash)
            raise RuntimeError(f"mypy exited {status}: {stderr[-300:]}")
        errors = _errors(stdout, set(present), str(workspace) + os.sep)
    shown = [f"{e['file']}:{e['line']}: {e['message']}" + (f" [{e['code']}]" if e["code"] else "") for e in errors]
    return {"pass": not errors and not missing, "n_errors": len(errors), "n_missing": len(missing),
            "n_files": len(paths), "errors": shown[:MAX_ERRORS], "missing": missing,
            "tool": TOOL, "tool_version": mypy.version.__version__}


def oracle(task: dict[str, Any], task_dir: Path) -> str:
    """No reference answer: the grade reads the workspace only."""
    del task, task_dir
    return ""


GRADERS = {"typecheck": grade}
ORACLES = {"typecheck": oracle}
