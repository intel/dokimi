# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a docker-* harness refuses before it starts, and the task's working tree.

Moved out of :mod:`.docker_solver` unchanged to keep that module under its
growth cap (``scripts/growth_baseline.json``); the names are re-imported there.
"""

from __future__ import annotations

import posixpath
from typing import Optional

from .container_agent import DOCKER_STEP_TIMEOUT, Docker
from .tasks import FORMAT_SWE_BENCH, FORMAT_TASK_MD, FORMAT_TBENCH_V1, TaskSpec

# The only tree a docker-* agent edits: elsewhere it gets root's home, whose .profile the root verifier sources.
# Why: an exported SkillsBench task works in /root; the `/app` rule is in docs/coding-harnesses.md.
_AGENT_TREE = "/app"
_IN_CONTAINER = "use docker-claude, whose agent runs in the task container"
_NO_ARM = "no docker-* arm runs such a task yet"


def _refuse_unrunnable(task: TaskSpec, mode: str, agent_location: str) -> None:
    """Refuse, from the task file alone, what a docker-* agent cannot run.

    - A ``task.md``, terminal-bench-v1 or SWE-bench task, for a host-side
      agent: its instruction names container paths (``/root/...``, ``/app/...``)
      and the agent runs on the host in a copy of ``/app``: a host write plus a
      false fail, as the verifier in the container finds nothing. The
      in-container agent runs such a task when it works in ``/app``.
    - A declared ``environment.workdir`` outside ``/app``, for either agent:
      both edit ``/app`` only (see :func:`_require_agent_workdir` for the
      image's own ``WORKDIR``, which needs the built image).
    - A task without the directory a mode copies in: ``tests/`` (``verifier/``)
      for the verifier, and ``solution/`` (``oracle/``) for ``docker-oracle``.
      ``docker cp`` would fail only at that step, after the image build and,
      for ``docker-claude``, the paid agent run.

    A probe is refused where its ``claude`` arm is: it would vouch for an arm
    that never runs. It copies neither directory. Oracle mode is refused only
    for a missing directory.
    """
    problem = task.local_docker_problem(oracle=mode == "oracle")
    if problem:
        raise ValueError(f"{task.name}: a docker-* harness cannot run it: {problem}")
    if mode != "probe":
        steps = [task.verifier_step] + ([task.oracle_step] if mode == "oracle" else [])
        for src, dest in (c for step in steps for c in step.copies):
            if not src.exists():
                what = f"{src.name}/ directory" if src in (task.solution_dir, task.tests_dir) else src.name
                raise ValueError(f"{task.name}: no {what} to copy to {dest}")
    if mode not in ("claude", "probe"):
        return
    if agent_location == "host" and task.format in (FORMAT_TASK_MD, FORMAT_TBENCH_V1, FORMAT_SWE_BENCH):
        raise ValueError(
            f"{task.name}: a {task.format} task cannot be solved by a host-side agent "
            f"(its instruction names absolute container paths); {_IN_CONTAINER}"
        )
    declared = _declared_workdir(task)
    if declared is not None and not _in_agent_tree(declared):
        raise ValueError(
            f"{task.name}: a docker-* agent edits only {_AGENT_TREE}; this task "
            f"declares environment.workdir = {declared!r}; {_NO_ARM}"
        )


def _require_agent_workdir(task: TaskSpec, container: str, log, docker: "Docker") -> str:
    """The task's working tree, refused before the agent starts unless it is in ``/app``.

    Both agents edit ``/app`` only: the host one a snapshot it copies back, the
    in-container one the tree it is ``chown``ed. An image that works in
    ``/root`` (77 of 101 SkillsBench images; a ``bench tasks export`` copy
    loads as Harbor) would otherwise get an agent in an ``/app`` that does not
    hold the task (a paid verified 0), or a ``chown`` that fails after the
    build. A ``WORKDIR`` inside ``/app`` (``/app/workspace``) runs, as on main;
    an image without that directory is refused with its name. This needs the
    built image, so ``--dry-run`` cannot see it.
    """
    workdir = _task_workdir(task, container, log, docker)
    if not _in_agent_tree(workdir):
        raise ValueError(
            f"{task.name}: the task works in {workdir}, but a docker-* agent edits "
            f"only {_AGENT_TREE}; {_NO_ARM}"
        )
    # Exit 1 is "no such directory"; 126/127 (no `test` in the image) leaves it to the copy.
    present = docker(["exec", "-u", "root", container, "test", "-d", workdir], log, check=False)
    if present.timed_out:  # not "present": no verdict, so the cell is errored before any agent runs
        raise RuntimeError(f"test -d {workdir} timed out after {DOCKER_STEP_TIMEOUT:g}s; see run.log")
    if present.returncode == 1:
        raise ValueError(
            f"{task.name}: the image has no {workdir}, the tree a docker-* agent edits; {_NO_ARM}"
        )
    return workdir


def _in_agent_tree(path: str) -> bool:
    """``path`` (normalised) is ``/app`` or below it."""
    return path == _AGENT_TREE or path.startswith(_AGENT_TREE + "/")


def _declared_workdir(task: TaskSpec) -> Optional[str]:
    """``environment.workdir`` normalised (``/.``, ``/app/..``), or ``None``."""
    declared = task.environment.get("workdir")
    if not (isinstance(declared, str) and declared.startswith("/")):
        return None
    return "/" + posixpath.normpath(declared).lstrip("/")  # normpath keeps a leading "//"


def _task_workdir(task: TaskSpec, container: str, log, docker: "Docker") -> str:
    """The task's working tree: ``environment.workdir``, else the image's
    ``WORKDIR``, else ``/app`` (the Harbor convention our own tasks follow).
    The agent works there only if it is ``/app`` or below it (see
    :func:`_require_agent_workdir`)."""
    declared = _declared_workdir(task)
    if declared is not None and declared != "/":
        # A declared "/" (also "/." or "/app/..") is ignored, like an image
        # WORKDIR of "/": a snapshot would `docker cp` the whole filesystem.
        return declared
    proc = docker(["inspect", "-f", "{{.Config.WorkingDir}}", container], log, check=False)
    if proc.timed_out:  # an unknown WORKDIR is not /app: the cell is errored, not billed on a guess
        raise RuntimeError(f"docker inspect of {container} timed out after {DOCKER_STEP_TIMEOUT:g}s "
                           "(WORKDIR unknown); see run.log")
    found = (proc.stdout or "").strip()
    if proc.returncode != 0 or not found.startswith("/"):
        return "/app"
    found = "/" + posixpath.normpath(found).lstrip("/")
    return found if found != "/" else "/app"
