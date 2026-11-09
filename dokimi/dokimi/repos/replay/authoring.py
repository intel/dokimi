# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The authoring-task writer (contract §4.8): one ``dokimi:hidden_tests`` task per :class:`Change`.

A change is a ``base`` and a ``target`` revision with a title and a description, whatever its
source: :mod:`.authoring_prs` makes one per merged pull request. The diff from ``base`` to
``target`` without the files :func:`.merged.left_out` names is split in two. Its test files
(:func:`is_test_path`) that it adds or changes are the hidden files, copied into the workspace
only when the task is graded; those that :func:`is_test_file` names are the hidden tests the
manifest's ``entry_points.test`` runs. The rest is the oracle: ``reference/`` holds each file it
adds or changes as ``target`` has it, and ``reference_deleted`` the ones it deletes.
"""

from __future__ import annotations

import json
import posixpath
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..graders.hidden_tests import HIDDEN_DIR
from ..graders.typecheck import REFERENCE_DIR
from ..manifest import RepoManifest
from . import authoring_prompt
from .history import History
from .merged import Skip, left_out

GRADER = "dokimi:hidden_tests"
_TEST_DIRS = frozenset({"test", "tests", "__tests__"})
_TEST_FILE = re.compile(r"^(?:test_.+|.+_tests?\.[^.]+|.+\.(?:test|spec)\.[^.]+)$")
_FIX = re.compile(r"^\s*(?:fix|bugfix|hotfix)\b", re.I)
_MODES = {"100644": 0o644, "100755": 0o755}


@dataclass(frozen=True)
class Change:
    """What the writer needs of a change; ``fields`` are its source's own ``task.json`` keys."""

    id: str
    number: int  # the pull request (or other) number a link to the change names
    title: str
    body: str
    base: str
    target: str
    public_at: str | None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Options:
    out: Path
    max_lines: int = 400
    instruction: str = authoring_prompt.DEFAULT_INSTRUCTION
    force: bool = False  # rewrite a task directory that already has a task.json
    fetch: bool = False  # fetch missing commits from origin (a cached clone; never a local repository)


@dataclass
class Outcome:
    """What became of one change: ``written``, ``kept`` (its task exists) or ``skipped``."""

    pr: int
    status: str
    task: str | None = None
    reason: str = ""
    n_hidden: int = 0
    n_lines: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def is_test_path(path: str) -> bool:
    """A test file, or any file under a ``test``, ``tests`` or ``__tests__`` directory, or a ``conftest.py``."""
    parts = path.split("/")
    return is_test_file(path) or parts[-1] == "conftest.py" or any(p in _TEST_DIRS for p in parts[:-1])


def is_test_file(path: str) -> bool:
    """A file a test runner collects by its name: ``test_*``, ``*_test.*``, ``*_tests.*``, ``*.test.*``, ``*.spec.*``."""
    return bool(_TEST_FILE.match(posixpath.basename(path)))


def family(title: str) -> str:
    """``fix`` for a title that starts with fix (``fix:``, ``fix(cli):``, ``bugfix``), else ``feature``."""
    return "fix" if _FIX.match(title) else "feature"


def kept(opts: Options, number: int, task_id: str) -> Outcome | None:
    """The outcome of a task directory that has a ``task.json`` and is not rewritten (no ``--force``), or ``None``."""
    if (opts.out / task_id / "task.json").exists() and not opts.force:
        return Outcome(number, "kept", task_id, "its task.json exists (--force rewrites it)")
    return None


def write(h: History, manifest: RepoManifest, change: Change, opts: Options) -> Outcome:
    """Write ``change``'s task to ``opts.out/<id>``; :class:`.merged.Skip` when it gives none."""
    task, files = _make(h, manifest, change, opts)
    _write(opts.out / change.id, task, files)
    return Outcome(change.number, "written", change.id, n_hidden=len(task["hidden_tests"]),
                   n_lines=task["oracle_lines"], dropped=task["prompt_dropped"])


def _make(h: History, manifest: RepoManifest, change: Change,
          opts: Options) -> tuple[dict[str, Any], dict[str, tuple[str, bytes]]]:
    """``(task.json, {task-dir path: (mode, content)})``; :class:`.merged.Skip` when the change gives no task."""
    changed = h.statuses(change.base, change.target)
    if not changed:
        raise Skip(f"{change.target[:12]} changes nothing against its base {change.base[:12]}")
    leave_out = sorted({p for _, p in changed if left_out(manifest, p)})
    rest = [(s, p) for s, p in changed if p not in leave_out]
    if not rest:
        raise Skip("it changes only guidance files or files under eval_dir")
    hidden = [p for s, p in rest if is_test_path(p) and s != "D"]
    tests = [p for p in hidden if is_test_file(p)]
    code = [(s, p) for s, p in rest if not is_test_path(p)]
    if not tests:
        raise Skip("it adds or changes no test file")
    if not code:
        raise Skip("it changes only test files, so there is nothing to implement")
    n_lines = h.n_lines_changed(change.base, change.target, [p for _, p in code])
    if n_lines > opts.max_lines:
        raise Skip(f"its diff outside the tests changes {n_lines} lines, more than --max-lines {opts.max_lines}")
    files = {f"{HIDDEN_DIR}/{p}": _blob(h, change.target, p) for p in hidden}
    files |= {f"{REFERENCE_DIR}/{p}": _blob(h, change.target, p) for s, p in code if s != "D"}
    prompt = _prompt(h, change, tests, leave_out, opts)
    if prompt.dropped["test_lines"] > authoring_prompt.MAX_LEAKED:
        raise Skip(f"its description names the hidden tests on {prompt.dropped['test_lines']} lines, more than "
                   f"{authoring_prompt.MAX_LEAKED}: the description is its tests")
    task: dict[str, Any] = {
        "id": change.id, "family": family(change.title), "base": change.base, "prompt": prompt.text, "grader": GRADER,
        "hidden_tests": tests, "hidden_files": hidden, **change.fields,
        "patch_files": sorted({p for _, p in rest}),  # the paths the change makes, tests too; as replay's
        "oracle_lines": n_lines, "prompt_dropped": prompt.dropped,
    }
    deleted = [p for s, p in code if s == "D"]
    if deleted:
        task["reference_deleted"] = deleted
    if leave_out:
        task["left_out_files"] = leave_out
    if change.public_at:
        task["public_at"] = change.public_at  # when the change was public (contract §4.6, §4.8)
    return task, files


def _blob(h: History, rev: str, path: str) -> tuple[str, bytes]:
    found = h.blob(rev, path)
    if found is None:
        raise Skip(f"{path} is not in {rev[:12]}")
    if found[0] not in _MODES:
        raise Skip(f"it makes {path} a symbolic link or a submodule, which a task copies no file for")
    return found


def _prompt(h: History, change: Change, tests: list[str], leave_out: list[str],
            opts: Options) -> authoring_prompt.Prompt:
    added, changed = _diff_lines(h.patch(change.base, change.target, leave_out))
    names = authoring_prompt.test_names(tests, [ln for p in tests for ln in added.get(p, [])])
    return authoring_prompt.build(change.title, change.body, names=names, changed_lines=changed,
                                  number=change.number, commits=h.commits(change.base, change.target),
                                  instruction=opts.instruction)


def _diff_lines(patch: bytes) -> tuple[dict[str, list[str]], set[str]]:
    """The lines a patch adds, by file; and every line it adds or removes, stripped, of 4+ characters."""
    added: dict[str, list[str]] = {}
    changed: set[str] = set()
    path = ""
    for raw in patch.decode("utf-8", "replace").split("\n"):
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
        elif raw.startswith(("+", "-")) and not raw.startswith("--- "):
            if raw[0] == "+":
                added.setdefault(path, []).append(raw[1:])
            if len(raw[1:].strip()) >= authoring_prompt.MIN_QUOTED:
                changed.add(raw[1:].strip())
    return added, changed


def _write(dest: Path, task: dict[str, Any], files: dict[str, tuple[str, bytes]]) -> None:
    for sub in (HIDDEN_DIR, REFERENCE_DIR):  # a rewrite keeps no file of an earlier build
        shutil.rmtree(dest / sub, ignore_errors=True)
    for rel, (mode, data) in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(_MODES[mode])
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
