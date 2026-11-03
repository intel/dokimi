# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``dokimi:typecheck``: the listed files type-check clean under the image's pinned mypy (contract §4.5).

task.json: ``"tool": "mypy"``, ``"version"`` (exactly the image's mypy version, else a grader
failure) and ``"paths"`` (workspace-relative files). ``pass`` is true when no error is reported
on a listed file, every listed file exists and nothing silences mypy. Errors in other modules are not counted.

It runs mypy in-process: the grader cannot start a child (the import layers allow spawns only in
``dokimi.process``, which the container cannot import), so basedpyright is not offered here. The
host-side L0 check (``repo scan --typecheck``) runs one, as ``--typecheck-tool`` selects. Standard library only at import
time; mypy is imported by :func:`grade` and must be importable by the image's ``python3``.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import sys
import tempfile
import tokenize
from pathlib import Path
from typing import Any

# Bump when a result for the same inputs changes (a metric or ``pass``).
VERSION = "1"
TOOL = "mypy"
REFERENCE_DIR = "reference"  # task dir: the files of a reference repair, copied over the workspace by oracle
MAX_ERRORS = 50  # reported in details; the count is in the metrics
_ERROR = re.compile(r"^(?P<file>.+?)(?::(?P<line>\d+))?(?::\d+)?: error: (?P<message>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$")
_TAIL = re.compile(r"^(?::(?P<line>\d+))?(?::\d+)?: error: (?P<message>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$")
# A comment that silences mypy: ``# type: ignore`` on a line, or a mypy directive comment (ignore-errors, ...).
# A listed file with one is not graded clean, whatever mypy then reports.
_SILENCE = re.compile(r"#\s*(?:type:\s*ignore|mypy:)")
_NO_TYPE_CHECK = "no_type_check"  # typing's decorator: mypy skips the body it wraps, under any import alias


def _safe_path(path: object) -> bool:
    """A workspace-relative path: no leading slash, no ``..`` segment, not empty, and one line: a line separator in
    a name would split it in mypy's output, so the error on that file would not be matched to it."""
    return (isinstance(path, str) and bool(path) and not path.startswith("/") and ".." not in path.split("/")
            and path.splitlines() == [path])


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
    elif len({posixpath.normpath(p) for p in paths}) != len(paths):  # "a.py" and "./a.py": one file, a duplicate module
        problems.append("paths must be distinct after normalising (\"a.py\" and \"./a.py\" are one file)")
    return problems


def _name(text: str, sep: str) -> str:
    """A diagnostic's file name as the listed names are: POSIX separators, normalised. Only ``sep`` (the native
    separator, which mypy prints on Windows) is converted, so a backslash in a POSIX name stays one."""
    return posixpath.normpath(text.replace(sep, "/"))


def _split(line: str, listed: set[str] | None, root: str, sep: str = os.sep) -> tuple[str, re.Match[str]] | None:
    """The file a mypy line is about, and the match of the diagnostic after it; None for a line that is not one.

    With ``listed``, the file is a listed name, normalised as the names are: a name may hold ": error: " itself, so
    the first separator in the line is not always the end of the name. Each separator is tried as the end of the
    name, and the last one that gives a listed name is taken (the longest name)."""
    if listed is None:
        m = _ERROR.match(line)
        if m is None:
            return None
        name = m.group("file")
        return _name(name[len(root):] if name.startswith(root) else name, sep), m
    text = line[len(root):] if line.startswith(root) else line
    best: tuple[str, re.Match[str]] | None = None
    for k, ch in enumerate(text):
        if ch != ":":
            continue
        tail = _TAIL.match(text[k:])
        name = _name(text[:k], sep)
        if tail is not None and name in listed:
            best = (name, tail)
    return best


def _errors(stdout: str, listed: set[str] | None, root: str, sep: str = os.sep) -> list[dict[str, Any]]:
    """The error lines of mypy's output on a listed file (any file when ``listed`` is None), in output
    order; ``root`` is stripped from the names, and ``sep`` (the separator mypy printed) is read as ``/``."""
    out = []
    for line in stdout.splitlines():
        hit = _split(line, listed, root, sep)
        if hit is None:
            continue
        name, m = hit
        if listed is None or name in listed:
            out.append({"file": name, "line": int(m.group("line") or 0),
                        "code": m.group("code") or "", "message": m.group("message")})
    return out


def _package_roots(workspace: Path, paths: list[str]) -> list[Path]:
    """For each listed file inside regular packages (``__init__.py``), the directory above its top package.

    A namespace directory (no ``__init__.py``) is not a package root: its files are named from the workspace, as
    an import names them, and nothing above them is added."""
    roots: list[Path] = []
    for p in paths:
        top = None
        d = (workspace / p).parent
        while d != workspace and (d / "__init__.py").is_file():
            top = d
            d = d.parent
        if top is not None and top.parent != workspace and top.parent not in roots:
            roots.append(top.parent)
    return roots


def _comments(text: str) -> list[tuple[int, str]]:
    """The comments of a Python source, with their lines: a marker inside a string or a docstring is not one.
    A source the tokenizer cannot read is taken line by line, so a marker in it still counts."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return list(enumerate(text.splitlines(), 1))


def _no_type_check(text: str) -> list[int]:
    """The lines where a source names ``no_type_check`` as code (not in a comment or a string); line by line for a
    source the tokenizer cannot read."""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return [tok.start[0] for tok in tokens if tok.type == tokenize.NAME and tok.string == _NO_TYPE_CHECK]
    except (tokenize.TokenError, SyntaxError):
        return [n for n, line in enumerate(text.splitlines(), 1) if _NO_TYPE_CHECK in line]


def _silenced(workspace: Path, listed: list[str]) -> list[str]:
    """Where the workspace silences mypy: ``file:line`` for a comment, the path for a stub (``.pyi`` shadows a module).

    A ``.pyi`` anywhere is refused: mypy_path puts the workspace first, so ``__getattr__ -> Any`` hides every attribute."""
    found = []
    for p in listed:
        text = (workspace / p).read_text(encoding="utf-8", errors="replace")
        found += [f"{p}:{n}" for n, comment in _comments(text) if _SILENCE.search(comment)]
        found += [f"{p}:{n}" for n in _no_type_check(text)]
    for dirpath, _, filenames in os.walk(workspace):
        found += [Path(dirpath, name).relative_to(workspace).as_posix() for name in filenames if name.endswith(".pyi")]
    return found


def _links_outside(workspace: Path) -> list[str]:
    """The symbolic links under the (resolved) workspace, a link to a directory included, whose target lies outside
    it. mypy follows an import through a link, so a link to a file this run cannot see (a hidden repair) would be read
    as the agent's own code, unlisted modules included. A link whose target stays inside is read as the file it names."""
    found = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        for name in (*dirnames, *filenames):
            path = Path(dirpath, name)
            if path.is_symlink() and not path.resolve().is_relative_to(workspace):
                found.append(path.relative_to(workspace).as_posix())
    return found


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
    linked_root = Path(run_dir or "/eval/run") / "workspace"  # unresolved: a link at the root must be seen
    workspace = linked_root.resolve()  # absolute: mypy gets these paths
    paths = [posixpath.normpath(p) for p in task["paths"]]
    # is_file() and mypy both follow links, and the grading container mounts the hidden reference: a listed path
    # with a link on the way (or a linked root) is missing, never read.
    missing = [p for p in paths
               if _linked_on_the_way(linked_root, linked_root / p) or not (workspace / p).is_file()]
    present = [p for p in paths if p not in missing]
    errors: list[dict[str, Any]] = []
    if present:
        outside = _links_outside(workspace)
        if outside:  # refused, not missing: the import of an unlisted module through it would be graded
            raise ValueError(f"{outside[0]} is a symbolic link to a file outside the workspace; mypy would follow it "
                             "to content this run cannot see")
        cache = tempfile.mkdtemp(prefix="dokimi-mypy-")
        neutral = tempfile.mkdtemp(prefix="dokimi-mypy-cfg-")
        limit = sys.getrecursionlimit()  # mypy's main raises it for the whole process
        # mypy reads MYPYPATH ahead of mypy_path, so an externally supplied stub directory could stand in for a
        # module the image lacks: it is out of the run, and restored afterwards.
        mypypath = os.environ.pop("MYPYPATH", None)
        try:
            # Neutral config: the subject's mypy.ini, pyproject.toml and the host's user config are not read.
            # mypy_path is the workspace and each regular package's source root (src/ for src/pkg/__init__.py).
            roots = [*_package_roots(workspace, present), workspace]
            bad = [str(r) for r in roots if re.search("[,:]", os.path.splitdrive(str(r))[1])]
            if bad:  # mypy splits mypy_path on , and :, so such a root would be read as two
                raise ValueError(f"a source root has , or : in its name, which mypy_path cannot hold: {bad[0]}")
            config = Path(neutral, "mypy.ini")
            config.write_text(f"[mypy]\nmypy_path = {':'.join(str(r) for r in roots)}\n", encoding="utf-8")
            # plain lines: the image's MYPY_FORCE_COLOR would put escapes around the file name and the marker
            stdout, stderr, status = api.run(["--no-incremental", "--no-color-output", f"--cache-dir={cache}",
                                              f"--config-file={config}", "--explicit-package-bases",
                                              "--check-untyped-defs",  # as L0: stripped annotations hide nothing
                                              "--show-absolute-path",  # cwd-independent names, matched to the listed ones
                                              *[str(workspace / p) for p in present]])
        finally:
            sys.setrecursionlimit(limit)
            if mypypath is not None:
                os.environ["MYPYPATH"] = mypypath
            shutil.rmtree(cache, ignore_errors=True)
            shutil.rmtree(neutral, ignore_errors=True)
        # 2 is also what mypy gives for a blocking source error (a syntax error, a duplicate module): those
        # are diagnostics like any other. A 2 with no diagnostic line is mypy itself failing.
        if status not in (0, 1) and not any(": error: " in line for line in stdout.splitlines()):
            raise RuntimeError(f"mypy exited {status}: {stderr[-300:]}")
        root = str(workspace) + os.sep
        errors = _errors(stdout, set(present), root)
        if status == 2 and not errors:
            # mypy stopped at a blocking error in a file it did not reach ("errors prevented further
            # checking"): the listed files were not checked, so that error is the grade's
            errors = _errors(stdout, None, root)
    silenced = _silenced(workspace, present)
    shown = [f"{e['file']}:{e['line']}: {e['message']}" + (f" [{e['code']}]" if e["code"] else "") for e in errors]
    shown += [f"{s}: mypy is silenced here (fix the code instead)" for s in silenced]
    return {"pass": not errors and not missing and not silenced, "n_errors": len(errors), "n_missing": len(missing),
            "n_suppressions": len(silenced), "n_files": len(paths), "errors": shown[:MAX_ERRORS], "missing": missing,
            "tool": TOOL, "tool_version": mypy.version.__version__}


def _linked_on_the_way(workspace: Path, dest: Path) -> bool:
    """True when the workspace root, a directory on the way to ``dest``, or ``dest`` itself is a symbolic link:
    a copy would then write to the link's target, which can lie outside the workspace."""
    if workspace.is_symlink():
        return True
    current = workspace
    for part in dest.relative_to(workspace).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def oracle(task: dict[str, Any], task_dir: Path, run_dir: Path | None = None) -> str:
    """No reference answer: the grade reads the workspace only. A task with a ``reference/`` directory
    has its files copied over the workspace first, so the reference repair is what gets graded. A destination
    reached through a symbolic link in the workspace is refused before anything is copied. A symbolic link in the
    reference is refused too: copytree would follow it, and the entries behind it are not walked here."""
    reference = task_dir / REFERENCE_DIR
    if reference.is_dir() and run_dir is not None:
        workspace = Path(run_dir) / "workspace"
        # checked even for an empty reference: copytree still copies the mode onto a linked root
        if _linked_on_the_way(workspace, workspace):
            raise ValueError("the workspace is a symbolic link; the reference would be written through it")
        for source in reference.rglob("*"):  # directories too: copytree creates an empty one through a link
            if source.is_symlink():
                raise ValueError(f"reference/{source.relative_to(reference).as_posix()} is a symbolic link; "
                                 "the reference is copied without links")
            dest = workspace / source.relative_to(reference)
            if _linked_on_the_way(workspace, dest):
                raise ValueError(f"{dest.relative_to(workspace).as_posix()} would be written through a "
                                 "symbolic link in the workspace")
        shutil.copytree(reference, workspace, dirs_exist_ok=True)
    return ""


GRADERS = {"typecheck": grade}
ORACLES = {"typecheck": oracle}
