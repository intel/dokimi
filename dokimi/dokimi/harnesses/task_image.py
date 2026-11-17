# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The image a ``docker-*`` task container starts from: built from the task, or Harbor's prebuilt one.

A Harbor task with ``[environment] docker_image`` (:attr:`.TaskSpec.docker_image`) runs on that
image, as ``harbor run`` does: the image is pulled only when the daemon lacks it (what Harbor's
``docker compose up`` does), and once the container runs, the task's loose ``environment/`` files
are copied into its workdir (:attr:`.TaskSpec.environment_upload`). Any other task builds
``dokimi/<task>:latest``. ``metrics.image`` records which ran, and a pulled image's id and digests.

The pull is the daemon's, like a ``docker build``'s base image: ``--network build=none``, which
takes the network away from a build's ``RUN`` steps, does not stop it.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from dokimi.docker_cmd import Docker

from .tasks import TaskSpec, _task_build_timeout

# One JSON object per image; RepoDigests is empty for an image that came from no registry.
_RECORD_FORMAT = '{"id": {{json .Id}}, "repo_digests": {{json .RepoDigests}}}'
# A reference starts with a letter or digit (distribution/reference); "-..." would be a docker flag.
_REFERENCE_RE = re.compile(r"[A-Za-z0-9]\S*")


def ready_image(task: TaskSpec, log, docker: Docker, build: Callable[[str], None]) -> tuple[str, dict[str, Any]]:
    """The image to start the task container from, and its ``metrics.image`` record.

    *build* builds the task's Dockerfile under the tag it is given. The pull gets the task's
    ``build_timeout_sec``: running out raises :class:`subprocess.TimeoutExpired`, as a build does.
    """
    image = task.docker_image
    if image is None:
        tag = f"dokimi/{task.name}:latest"
        build(tag)
        return tag, {"source": "build", "ref": tag}
    if not _REFERENCE_RE.fullmatch(image):
        raise ValueError(f"{task.name}: [environment] docker_image {image!r} is not a docker image reference")
    if (task.build_context / task.dockerfile).is_file():
        log.write(f"[image] {task.dockerfile} not built: [environment] docker_image {image} wins, as in Harbor\n")
    inspect = ["image", "inspect", "-f", _RECORD_FORMAT, image]
    found = docker(inspect, log, check=False)
    pulled = found.returncode != 0
    if pulled:
        docker(["pull", "-q", image], log, timeout=_task_build_timeout(task))
        found = docker(inspect, log)
    return image, {"source": "docker_image", "ref": image, "pulled": pulled, **json.loads(found.stdout or "")}


def workdir_args(task: TaskSpec) -> list[str]:
    """``-w`` for a prebuilt-image task's ``environment.workdir``: Harbor runs every exec there
    (``DockerEnvironment.exec``: ``cwd or task_env_config.workdir``). Harbor does so for a built task
    too; a built task keeps its image's ``WORKDIR`` here on purpose, as on main."""
    workdir = task.environment.get("workdir")
    return ["-w", workdir] if task.docker_image is not None and isinstance(workdir, str) and workdir else []


def upload_environment(task: TaskSpec, container: str, log, docker: Docker) -> None:
    """Copy :attr:`.TaskSpec.environment_upload` into the started container, as Harbor does.

    Into ``environment.workdir``, else the image's ``WORKDIR``, else ``/`` (Harbor's
    ``_upload_environment_dir_after_start`` runs ``pwd``). A top-level ``skills/`` stays out, as
    it stays out of a build context.
    """
    src = task.environment_upload
    if src is None:
        return
    workdir = task.environment.get("workdir")
    if not isinstance(workdir, str) or not workdir:
        workdir = (docker(["inspect", "-f", "{{.Config.WorkingDir}}", container], log).stdout or "").strip() or "/"
    with tempfile.TemporaryDirectory(prefix="dokimi-upload-") as tmp:
        if task.skills_dir is not None:
            staged = Path(tmp) / "environment"
            shutil.copytree(src, staged, symlinks=True, ignore=ignore_top_level_skills(src))
            src = staged
        docker(["exec", "-u", "root", container, "mkdir", "-p", workdir], log)
        docker(["cp", f"{src}/.", f"{container}:{workdir}"], log)


def ignore_top_level_skills(env_dir: Path):
    """``shutil.copytree`` ignore hook that drops only ``<env_dir>/skills``."""
    def ignore(directory: str, names: list[str]) -> list[str]:
        return ["skills"] if Path(directory) == env_dir and "skills" in names else []
    return ignore
