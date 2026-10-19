# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Entry points in the image, ``grade.json`` (``repo_grade.v1``) and the gate (contract §3, §4.2).

``grade`` and ``oracle`` run in a container of their own after any agent has
exited. It mounts the run directory read-write at ``/eval/run``, the exported
eval dir read-only at ``/eval/content`` and, only for a template that names
``{source_dir}``, the full-history source read-only at ``/eval/source``. A task
whose ``grader`` is ``dokimi:<name>`` runs a built-in grader
(:mod:`dokimi.repos.graders`, mounted read-only at ``/eval/graders``) in that
container instead of the manifest's entry point (contract §4.5).

A grader failure is a failure of the run on every gated metric: ``grade``
exiting non-zero, timing out, writing no valid ``grade.json``, or writing
``pass: null`` with errors. Excluding such runs would favour arms that break
workspaces.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokimi.artifacts import schema_errors

from .container import (
    CONTENT_DIR,
    GRADERS_DIR,
    INFRA_EXIT,
    RUN_DIR,
    SOURCE_DIR,
    WORKSPACE,
    ContainerError,
    ContainerResult,
    ContainerRunner,
    ContainerSpec,
    Mount,
)
from .graders import ENTRY as BUILTIN_ENTRY
from .graders import HOST_DIR as GRADERS_HOST
from .graders import NAMES as BUILTIN_NAMES
from .graders import PREFIX as BUILTIN_PREFIX
from .graders import builtin_name
from .manifest import RepoManifest
from .workspace import Task

GRADE_SCHEMA = "repo_grade.v1"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
CONTAINER_PATHS = {"run_dir": RUN_DIR, "eval_dir": CONTENT_DIR, "source_dir": SOURCE_DIR}
GRADE_HOME = "grade-home"  # under {run_dir}, which the agent's container never mounts
AGENT_GIT = "agent-git"    # where the workspace's own .git/config and hooks are moved before grading
# Command-line git configuration outranks .git/config: an fsmonitor or hook the agent set never runs.
_GRADER_GIT_CONFIG = (("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null"))
_PLAIN_GIT_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n"
# Run-dir files the framework reads or writes after a grader had the run dir read-write.
_NOT_FOUND_EXIT = 127  # docker run: the command (here the image's python3) is not on PATH
HOST_FILES = ("meta.json", "task.json", "answer.txt", "events.jsonl", "result.json", "grade.log", "grade.err")


@dataclass
class EvalContext:
    """What every container of one evaluation needs: the trees, the image and the caches."""

    manifest: RepoManifest
    git_dir: Path              # full-history git dir task bases are checked out from
    content_dir: Path          # host side of {eval_dir}
    image: str
    runner: ContainerRunner
    caches: dict[str, Path] = field(default_factory=dict)  # cache name -> host directory
    source_dir: Path | None = None  # host side of {source_dir}
    entry_timeout_s: float = 3600

    def cache_mounts(self) -> list[Mount]:
        image = self.manifest.image
        return [Mount(self.caches[c.name], c.path) for c in (image.caches if image else ())]

    def base_env(self, task: Task) -> dict[str, str]:
        """HOME, USER and ``image.env``, the same in the agent's and the graders' containers."""
        env = {"HOME": f"{RUN_DIR}/home", "USER": "eval"}
        values = {**CONTAINER_PATHS, "task": task.id}
        for name, value in (self.manifest.image.env if self.manifest.image else {}).items():
            env[name] = substitute(value, values)
        return env

    def grade_env(self, task: Task) -> dict[str, str]:
        """:meth:`base_env` with a ``HOME`` the agent never had, no user site-packages and git
        configuration that disables what the agent could have set to run code in the grader."""
        env = {**self.base_env(task), "HOME": f"{RUN_DIR}/{GRADE_HOME}", "PYTHONNOUSERSITE": "1"}
        env["GIT_CONFIG_COUNT"] = str(len(_GRADER_GIT_CONFIG))
        for i, (key, value) in enumerate(_GRADER_GIT_CONFIG):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
        return env


def substitute(template: str, values: dict[str, str]) -> str:
    """Replace known ``{placeholders}`` in one pass; a substituted value is never substituted again."""
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def render_entry(template: str, task: Task) -> list[str]:
    """An entry point as argv: split like a shell would, then substitute each word. No shell runs it."""
    return [substitute(word, {**CONTAINER_PATHS, "task": task.id}) for word in shlex.split(template)]


def run_entry(ctx: EvalContext, kind: str, task: Task, run_dir: Path, network: str,
              name: str | None = None) -> ContainerResult:
    """Run entry point ``kind`` (grade, oracle) for the run in ``run_dir``; logs go to ``<kind>.log``.

    A task whose ``grader`` is ``dokimi:<name>`` runs the framework's built-in grader instead of the
    manifest's entry point, in the same container (contract §4.5)."""
    builtin = builtin_name(task.raw.get("grader"))
    argv, extra = _builtin_entry(ctx, kind, task, builtin) if builtin is not None else _repo_entry(ctx, kind, task)
    home = run_dir / GRADE_HOME
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir()
    _set_git_config_aside(run_dir)
    if kind == "grade":
        (run_dir / "grade.json").unlink(missing_ok=True)  # never read a previous grade as this one
    for log in (f"{kind}.log", f"{kind}.err"):  # an earlier entry point's link would be opened for writing
        (run_dir / log).unlink(missing_ok=True)
    mounts = [Mount(run_dir.resolve(), RUN_DIR), Mount(ctx.content_dir.resolve(), CONTENT_DIR, read_only=True)]
    spec = ContainerSpec(image=ctx.image, argv=argv, workdir=WORKSPACE, mounts=mounts + extra + ctx.cache_mounts(),
                         env=ctx.grade_env(task), network=network, name=name)
    result = ctx.runner.run(spec, stdout=run_dir / f"{kind}.log", stderr=run_dir / f"{kind}.err",
                            timeout_s=ctx.entry_timeout_s)
    if builtin is not None and result.returncode == _NOT_FOUND_EXIT:
        raise ContainerError(f"{task.raw['grader']} runs on the image's python3, and the image has none on PATH "
                             f"(`{kind}` exited {_NOT_FOUND_EXIT}; see {run_dir / f'{kind}.err'})")
    return result


def _repo_entry(ctx: EvalContext, kind: str, task: Task) -> tuple[list[str], list[Mount]]:
    """argv of the manifest's entry point ``kind``, and ``{source_dir}`` when it names it."""
    template = ctx.manifest.entry_points.get(kind)
    if not template:
        raise ContainerError(f"the manifest declares no `{kind}` entry point")
    if "{source_dir}" not in template:
        return render_entry(template, task), []
    if ctx.source_dir is None:
        raise ContainerError(f"`{kind}` uses {{source_dir}} but no full-history source is available")
    return render_entry(template, task), [Mount(ctx.source_dir.resolve(), SOURCE_DIR, read_only=True)]


def _builtin_entry(ctx: EvalContext, kind: str, task: Task, builtin: str) -> tuple[list[str], list[Mount]]:
    """argv of built-in grader ``builtin`` for ``kind``, and the read-only mount of its code.

    ``-I``: no ``PYTHONPATH`` from ``image.env``, no user site and no script directory on ``sys.path``,
    so nothing the agent wrote can stand in for a standard-library module."""
    if builtin not in BUILTIN_NAMES:
        raise ContainerError(f"unknown built-in grader {task.raw['grader']!r}; known: "
                             + ", ".join(BUILTIN_PREFIX + n for n in BUILTIN_NAMES))
    try:
        rel = task.dir.resolve().relative_to(ctx.content_dir.resolve())
    except ValueError:
        raise ContainerError(f"task {task.id}: its directory {task.dir} is not in the exported eval dir") from None
    argv = ["python3", "-I", "-B", f"{GRADERS_DIR}/{BUILTIN_ENTRY}", kind, builtin,
            f"{CONTENT_DIR}/{rel.as_posix()}", RUN_DIR]
    return argv, [Mount(GRADERS_HOST, GRADERS_DIR, read_only=True)]


def _set_git_config_aside(run_dir: Path) -> None:
    """Move the workspace's ``.git/config``, hooks and other config sources to ``agent-git/`` and
    write a plain config: a filter, diff driver, alias or include the agent set does not run in the
    grader. The first move is kept, so a regrade does not overwrite the agent's original."""
    dot_git = run_dir / "workspace" / ".git"
    aside = run_dir / AGENT_GIT
    first = not aside.exists()
    aside.mkdir(exist_ok=True)
    if dot_git.is_symlink() or dot_git.is_file():  # the agent's own repository elsewhere: the grader gets none
        shutil.move(str(dot_git), str(aside / "dot-git"))
        return
    if not dot_git.is_dir():
        return
    for name in ("config", "config.worktree", "commondir", "hooks"):
        path = dot_git / name
        if not (path.exists() or path.is_symlink()):
            continue
        if first:
            shutil.move(str(path), str(aside / name))
        elif path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    (dot_git / "config").write_text(_PLAIN_GIT_CONFIG, encoding="utf-8")


def check_grade(doc: Any) -> list[str]:
    """Problems with a ``grade.json`` document; empty when it is a valid ``repo_grade.v1``."""
    return schema_errors(GRADE_SCHEMA, doc)


def replaced_files(run_dir: Path) -> list[str]:
    """:data:`HOST_FILES` in ``run_dir`` that are a link, a directory or anything but a regular file:
    the grader runs the agent's code, and the host must not read or write through what it left."""
    out = []
    for name in HOST_FILES:
        try:
            if not stat.S_ISREG(os.lstat(run_dir / name).st_mode):
                out.append(name)
        except FileNotFoundError:
            pass
    return out


def read_grade(run_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = run_dir / "grade.json"
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None, ["grade.json is not a regular file"]
        # O_NOFOLLOW: a link swapped in after the lstat is not followed either
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as f:
            doc = json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return None, ["no grade.json written"]
    except (OSError, ValueError) as exc:
        return None, [f"grade.json is not JSON: {exc}"]
    problems = check_grade(doc)
    return (None, [f"grade.json is not {GRADE_SCHEMA}: {p}" for p in problems]) if problems else (doc, [])


def gate_ok(task: Task, grade: dict[str, Any]) -> bool:
    """Whether a valid grade passes the task's ``gate`` (default ``["pass"]``)."""
    metrics = grade.get("metrics") or {}
    for entry in task.raw.get("gate", ["pass"]):
        if isinstance(entry, dict):
            value = metrics.get(entry["metric"])
            if not _is_number(value) or value < entry["min"]:
                return False
        else:
            value = grade.get("pass") if entry == "pass" else metrics.get(entry)
            if value is not True:
                return False
    return True


def score_value(task: Task, grade: dict[str, Any] | None) -> float | None:
    if not task.score or grade is None:
        return None
    value = (grade.get("metrics") or {}).get(task.score["metric"])
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def outcome(task: Task, run_dir: Path, result: ContainerResult, error: str | None = None) -> dict[str, Any]:
    """The framework's verdict on one ``grade`` run (contract §4.2); ``error`` when the run itself failed."""
    grade, problems = read_grade(run_dir)
    reasons = []
    if error:
        reasons.append(error)
    elif result.timed_out:
        reasons.append("grade timed out")
    elif result.returncode != 0:
        reasons.append(f"grade exited {result.returncode}")
    reasons += problems
    reasons += [f"{name} is not a regular file after grading" for name in replaced_files(run_dir)]
    if grade is not None and grade["pass"] is None and grade["errors"]:
        reasons.append("grader error: " + str(grade["errors"][0]).strip().splitlines()[0][:300])
    elif grade is not None and grade["pass"] is None and not task.score:
        # score-only is a property of the task, not of one grade: a gated task's grade must decide
        reasons.append("grade.json is score-only (pass: null) but task.json has no `score`")
    if not reasons and task.score and score_value(task, grade) is None:
        # a missing score would leave the mean, while a grader failure counts as 0
        reasons.append(f"grade.json has no numeric metrics.{task.score['metric']} (task.json `score`)")
    failed = bool(reasons)
    score_only = not failed and grade is not None and grade["pass"] is None
    return {
        "status": "grader_failure" if failed else "graded",
        "pass": None if grade is None else grade["pass"],
        "success": None if score_only else (False if failed else gate_ok(task, grade or {})),
        "score": None if failed else score_value(task, grade),
        "score_only": score_only,
        "reasons": reasons,
        "rc": result.returncode,
        "timed_out": result.timed_out,
        "wall_s": result.wall_s,
    }


def failed_outcome(reason: str) -> dict[str, Any]:
    """:func:`outcome` for a run that was never graded (the grade could not start, or the host failed
    around it): a grader failure that reads nothing from the run dir."""
    return {"status": "grader_failure", "pass": None, "success": False, "score": None, "score_only": False,
            "reasons": [reason], "rc": INFRA_EXIT, "timed_out": False, "wall_s": 0.0}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
