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

:class:`DockerRunner` drives the docker CLI through :mod:`dokimi.process`.
Tests use a fake runner with the same :class:`ContainerRunner` interface, or
give :class:`DockerRunner` a :class:`dokimi.process.FakeRunner`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dokimi import process

RUN_DIR = "/eval/run"
WORKSPACE = f"{RUN_DIR}/workspace"
CONTENT_DIR = "/eval/content"
SOURCE_DIR = "/eval/source"
HARNESS_DIR = "/eval/harness"
SUPPORT_DIR = "/eval/support"
NETWORKS = ("none", "bridge", "host")
# Predefined docker build args: passed by name when set, so a build behind a proxy works
# and the values stay out of the image history.
PROXY_BUILD_ARGS = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
PIDS_LIMIT = 32768  # threads count too: a Bazel server and a -j build on a many-core host use thousands
INFRA_EXIT = 125  # `docker run` itself failed (daemon, mount, image); the command never started
BUILD_TIMEOUT_S = 3 * 3600  # default: a wedged daemon or a hung RUN step fails the build instead of hanging the batch
DOCKER_CALL_TIMEOUT_S = 60  # `docker kill`, `docker image inspect`
STOP_GRACE_S = 10  # a timed-out docker CLI gets SIGTERM (it stops the container) before SIGKILL


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

    def __init__(self, docker: str = "docker", runner: process.Runner = process.run):
        self.docker = docker
        self.runner = runner

    def argv(self, spec: ContainerSpec) -> list[str]:
        if spec.network not in NETWORKS:
            raise ContainerError(f"unknown network {spec.network!r} (known: {', '.join(NETWORKS)})")
        out = [self.docker, "run", "--rm", "--init", "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
               "--pids-limit", str(PIDS_LIMIT), "--network", spec.network, "-w", spec.workdir]
        if spec.name:
            out += ["--name", spec.name]
        user = spec.user or host_user()
        if user:
            out += ["--user", user]
        for m in spec.mounts:
            if "," in str(m.host) or "," in m.container:
                raise ContainerError(f"a mount path cannot contain ',': {m.host} -> {m.container}")
            out += ["--mount", f"type=bind,src={m.host},dst={m.container}" + (",readonly" if m.read_only else "")]
        for name, value in spec.env.items():
            out += ["-e", f"{name}={value}"]
        for name in spec.pass_env:
            out += ["-e", name]
        return [*out, "--entrypoint", "", spec.image, *spec.argv]

    def run(self, spec: ContainerSpec, *, stdout: Path, stderr: Path, timeout_s: float) -> ContainerResult:
        for m in spec.mounts:
            if not m.host.exists():
                raise ContainerError(f"mount source does not exist: {m.host}")

        def stop() -> None:
            if spec.name:
                try:
                    self.runner([self.docker, "kill", spec.name], timeout=DOCKER_CALL_TIMEOUT_S,
                                env=process.Env.inherit())
                except OSError:
                    pass  # still a timed-out run; the docker CLI is gone, the container may outlive it

        # -e NAME reads the forwarded values from the CLI's own environment: the caller's.
        result = run_process(self.argv(spec), env=process.Env.inherit(), stdout=stdout, stderr=stderr,
                             timeout_s=timeout_s, on_timeout=stop, runner=self.runner)
        if result.returncode == INFRA_EXIT and not result.timed_out:
            tail = stderr.read_text(encoding="utf-8", errors="replace").strip()[-500:]
            raise ContainerError(f"docker run failed: {tail}")
        return result

    def build(self, context: Path, dockerfile: Path, tag: str, network: str | None = None,
              timeout_s: float = BUILD_TIMEOUT_S) -> None:
        argv = [self.docker, "build", "-f", str(dockerfile), "-t", tag]
        if network:
            argv += ["--network", network]
        for name in PROXY_BUILD_ARGS:
            if name in os.environ:
                argv += ["--build-arg", name]
        try:
            proc = self.runner([*argv, str(context)], timeout=timeout_s, env=process.Env.inherit())
        except OSError as exc:
            raise ContainerError(f"docker build {tag}: cannot run {self.docker!r}: {exc}") from exc
        if proc.timed_out:
            raise ContainerError(f"docker build {tag} timed out after {timeout_s:g}s (--build-timeout):\n"
                                 f"{(proc.stderr or proc.stdout)[-3000:]}")
        if proc.returncode:
            raise ContainerError(f"docker build {tag} failed:\n{(proc.stderr or proc.stdout)[-3000:]}")

    def image_exists(self, tag: str) -> bool:
        proc = self.runner([self.docker, "image", "inspect", tag], timeout=DOCKER_CALL_TIMEOUT_S,
                           env=process.Env.inherit(), capture_stdout=False)
        if proc.timed_out:  # a daemon that does not answer would only hang the build next
            raise ContainerError(f"docker image inspect {tag}: no answer in {DOCKER_CALL_TIMEOUT_S}s")
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
