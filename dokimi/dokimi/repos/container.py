# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Containers for agent runs and entry points (contract §1.1, §2).

Every container is started the same way, whatever the image's ``ENTRYPOINT``:

- ``--entrypoint ''``, so the spec's argv is the whole command;
- ``--init``, so a daemonizing build server (Bazel) is reaped and its cache
  writes are not lost on shutdown;
- ``--security-opt no-new-privileges`` and ``--cap-drop ALL``, so a sudo or
  setuid binary in the image cannot make the command root and leave root-owned
  files in the bind mounts; ``--pids-limit`` against a fork bomb, high enough
  for a parallel build;
- the caller's uid and gid, so files in the run directory stay the caller's;
  ``HOME`` and ``USER`` are always set, because a uid with no passwd entry
  breaks ``getpass`` and Bazel's ``$USER``;
- fixed paths: ``{run_dir}`` is ``/eval/run``, ``{eval_dir}`` ``/eval/content``
  and ``{source_dir}`` ``/eval/source``;
- every mount is an explicit bind mount (caches and ``HOME`` never land on the
  container layer, which once filled the docker root);
- the framework's environment (``HOME``, ``USER``, ``image.env``, the profile's
  ``env_set``) is passed as ``-e NAME=value``; host values the harness profile
  allow-lists (credentials among them) are passed by name (``-e NAME``) from
  the docker CLI's own environment, so a credential never appears in an argv
  or a process list. The CLI itself runs with the host environment: the
  container's ``HOME`` would hide ``~/.docker`` and break rootless runtimes.

:class:`DockerRunner` builds the docker argv and starts it through
:mod:`dokimi.docker_cmd` (the one module that spells docker) and
:mod:`dokimi.process`. Tests use a fake runner with the same
:class:`ContainerRunner` interface, or give :class:`DockerRunner` a
:class:`dokimi.process.FakeRunner`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dokimi import docker_cmd, process
from dokimi.container_policy import ContainerPolicy
from dokimi.docker_cmd import (  # noqa: F401  (re-exported: the names callers have always imported from here)
    BUILD_TIMEOUT_S,
    INFRA_EXIT,
    PIDS_LIMIT,
    PROXY_BUILD_ARGS,
    STOP_GRACE_S,
)
from dokimi.docker_cmd import CALL_TIMEOUT_S as DOCKER_CALL_TIMEOUT_S  # noqa: F401

RUN_DIR = "/eval/run"
WORKSPACE = f"{RUN_DIR}/workspace"
CONTENT_DIR = "/eval/content"
SOURCE_DIR = "/eval/source"
HARNESS_DIR = "/eval/harness"
SUPPORT_DIR = "/eval/support"
GRADERS_DIR = "/eval/graders"  # the built-in graders, mounted read-only into the grading container only
NETWORKS = ("none", "bridge", "host")


class ContainerError(RuntimeError):
    """The container runtime failed, as opposed to the command inside it."""


class EgressNotImplemented(ContainerError):
    """A run needs per-host egress (contract §2), which needs the proxy the framework does not have yet."""


@dataclass(frozen=True)
class Mount:
    host: Path
    container: str
    read_only: bool = False


@dataclass
class ContainerSpec:
    image: str
    argv: list[str]
    workdir: str = WORKSPACE
    mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)  # values set in the container
    pass_env: tuple[str, ...] = ()  # host variables forwarded by name (credentials)
    network: str = "none"
    name: str | None = None
    user: str | None = None


@dataclass
class ContainerResult:
    returncode: int | None
    timed_out: bool
    wall_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class ContainerRunner(Protocol):
    def run(self, spec: ContainerSpec, *, stdout: Path, stderr: Path, timeout_s: float) -> ContainerResult: ...

    def build(self, context: Path, dockerfile: Path, tag: str, network: str | None = None,
              timeout_s: float = BUILD_TIMEOUT_S) -> None: ...

    def image_exists(self, tag: str) -> bool: ...


def host_user() -> str | None:
    """``uid:gid`` of this process, or ``None`` where there is no POSIX uid (Windows)."""
    if hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"
    return None


class DockerRunner:
    """Run containers with the docker CLI."""

    def __init__(self, docker: str = docker_cmd.BINARY, runner: process.Runner = process.run):
        self.docker = docker
        self.runner = runner

    def argv(self, spec: ContainerSpec) -> list[str]:
        if spec.network not in NETWORKS:
            raise ContainerError(f"unknown network {spec.network!r} (known: {', '.join(NETWORKS)})")
        flags = ContainerPolicy(network=spec.network, init=True, no_new_privileges=True, cap_drop_all=True,
                                pids_limit=PIDS_LIMIT)
        out = [self.docker, "run", "--rm", *flags.init_argv(), *flags.hardening_argv(), *flags.pids_argv(),
               *flags.network_argv(), "-w", spec.workdir]
        if spec.name:
            out += ["--name", spec.name]
        user = spec.user or host_user()
        if user:
            out += ["--user", user]
        try:
            out += docker_cmd.mount_args((m.host, m.container, m.read_only) for m in spec.mounts)
        except ValueError as exc:
            raise ContainerError(str(exc)) from exc
        out += docker_cmd.env_args(spec.env, spec.pass_env)
        return [*out, "--entrypoint", "", spec.image, *spec.argv]

    def run(self, spec: ContainerSpec, *, stdout: Path, stderr: Path, timeout_s: float) -> ContainerResult:
        for m in spec.mounts:
            if not m.host.exists():
                raise ContainerError(f"mount source does not exist: {m.host}")

        def stop() -> None:
            if spec.name:
                try:
                    self.runner([self.docker, *docker_cmd.kill_args(spec.name)], timeout=docker_cmd.CALL_TIMEOUT_S,
                                env=process.Env.inherit())
                except OSError:
                    pass  # still a timed-out run; the docker CLI is gone, the container may outlive it

        # -e NAME reads the forwarded values from the CLI's own environment: the caller's.
        result = run_process(self.argv(spec), env=process.Env.inherit(), stdout=stdout, stderr=stderr,
                             timeout_s=timeout_s, on_timeout=stop, runner=self.runner)
        if docker_cmd.failed_to_start(result.returncode, result.timed_out):
            tail = stderr.read_text(encoding="utf-8", errors="replace").strip()[-500:]
            raise ContainerError(f"docker run failed: {tail}")
        return result

    def build(self, context: Path, dockerfile: Path, tag: str, network: str | None = None,
              timeout_s: float = BUILD_TIMEOUT_S) -> None:
        argv = [self.docker, *docker_cmd.build_args(context, dockerfile, tag, network=network)]
        try:
            proc = self.runner(argv, timeout=timeout_s, env=process.Env.inherit())
        except OSError as exc:
            raise ContainerError(f"docker build {tag}: cannot run {self.docker!r}: {exc}") from exc
        if proc.timed_out:
            raise ContainerError(f"docker build {tag} timed out after {timeout_s:g}s (--build-timeout):\n"
                                 f"{(proc.stderr or proc.stdout)[-3000:]}")
        if proc.returncode:
            raise ContainerError(f"docker build {tag} failed:\n{(proc.stderr or proc.stdout)[-3000:]}")

    def image_exists(self, tag: str) -> bool:
        proc = self.runner([self.docker, *docker_cmd.inspect_image_args(tag)], timeout=docker_cmd.CALL_TIMEOUT_S,
                           env=process.Env.inherit(), capture_stdout=False)
        if proc.timed_out:  # a daemon that does not answer would only hang the build next
            raise ContainerError(f"docker image inspect {tag}: no answer in {docker_cmd.CALL_TIMEOUT_S}s")
        return proc.returncode == 0


def run_process(argv: list[str], *, env: process.Env | dict[str, str], stdout: Path, stderr: Path,
                timeout_s: float, cwd: Path | None = None, on_timeout: Callable[[], None] | None = None,
                runner: process.Runner = process.run) -> ContainerResult:
    """Run ``argv`` with its output in the files ``stdout`` and ``stderr``. On a timeout or an
    interrupt its process group gets SIGTERM, then SIGKILL after :data:`STOP_GRACE_S`; after a
    timeout ``on_timeout`` runs. A ``dict`` ``env`` is exactly the child's environment.

    When the command exits, whatever it left running in its group is killed."""
    policy = env if isinstance(env, process.Env) else process.Env.only((), env)
    done = runner(argv, env=policy, cwd=cwd, timeout=timeout_s, stdout_to=stdout, stderr_to=stderr,
                  term_grace=STOP_GRACE_S)
    if done.timed_out and on_timeout is not None:
        on_timeout()
    return ContainerResult(returncode=None if done.timed_out else done.returncode, timed_out=done.timed_out,
                           wall_s=round(done.elapsed_sec, 1))


def docker_network(network: str, egress: tuple[str, ...] = ()) -> str:
    """The docker network for a manifest ``image.network`` plus a task's ``egress`` (contract §2)."""
    if network == "public":
        return "bridge"
    if network != "none":
        raise ContainerError(f"unknown image.network {network!r}")
    if egress:
        raise EgressNotImplemented(
            "egress proxy not implemented: this run needs " + ", ".join(egress)
            + " through the contract §2 proxy; it is refused rather than given the full network")
    return "none"
