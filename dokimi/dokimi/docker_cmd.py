# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The docker CLI, spelled once.

Every ``docker`` command the package runs is an argv from :func:`argv` (or a
:class:`dokimi.repos.container.DockerRunner` built from :data:`BINARY`), and every
docker process is started through :mod:`dokimi.process`. This module owns what
more than one call site shares: the binary, the step and build timeouts, the
mount and ``-e`` spellings, the hardening of a task container, the build's
proxy build-args, and the exit code of a ``docker run`` that failed before the
command started.

It starts nothing itself. Policy stays with the caller: which network a phase
gets (:mod:`dokimi.harnesses.network_policy`), what a failed step means, and the
order of a call's flags, which each call site keeps as its argv has always had it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping

from dokimi import process

BINARY = "docker"
PIDS_LIMIT = 32768  # threads count too: a Bazel server and a -j build on a many-core host use thousands
BUILD_TIMEOUT_S = 3 * 3600  # default: a wedged daemon or a hung RUN step fails the build instead of hanging the batch
CALL_TIMEOUT_S = 60  # `docker kill`, `docker image inspect`
STEP_TIMEOUT_S = 600.0  # a docker step without a budget of its own (cp, exec, inspect, ...)
STOP_GRACE_S = 10  # a timed-out docker CLI gets SIGTERM (it stops the container) before SIGKILL
INFRA_EXIT = 125  # `docker run` itself failed (daemon, mount, image); the command never started
# Predefined docker build args: passed by name when set, so a build behind a proxy works
# and the values stay out of the image history.
PROXY_BUILD_ARGS = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
# A task container: the repo container's hardening, with the capabilities root needs for its own
# steps back (CHOWN, FOWNER, DAC_OVERRIDE; SETUID/SETGID for task scripts that drop privileges).
TASK_CONTAINER_HARDENING = (
    "--init", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--pids-limit", str(PIDS_LIMIT),
    "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
    "--cap-add", "SETUID", "--cap-add", "SETGID",
)

#: The callable a harness passes as its docker step: ``docker(args, log, check=..., timeout=...)``.
Docker = Callable[..., process.Completed]


def argv(*args: str) -> list[str]:
    """``docker *args`` as the argv to start: the one place the binary's name is written."""
    return [BINARY, *args]


def mount_args(mounts: Iterable[tuple[str | os.PathLike[str], str, bool]]) -> list[str]:
    """``--mount type=bind`` for each ``(host path, container path, read_only)``. A comma would
    split the ``--mount`` value, so a path with one is refused (``ValueError``)."""
    out: list[str] = []
    for host, dst, read_only in mounts:
        src = os.fspath(host)
        if "," in src or "," in dst:
            raise ValueError(f"a mount path cannot contain ',': {src} -> {dst}")
        out += ["--mount", f"type=bind,src={src},dst={dst}" + (",readonly" if read_only else "")]
    return out


def env_args(set: Mapping[str, str] | None = None, pass_names: Iterable[str] = ()) -> list[str]:
    """``-e NAME=value`` for each ``set`` item, then ``-e NAME`` for each ``pass_names``: a value
    passed by name is read from the CLI's own environment, so it never shows in an argv."""
    out: list[str] = []
    for name, value in (set or {}).items():
        out += ["-e", f"{name}={value}"]
    for name in pass_names:
        out += ["-e", name]
    return out


def build_args(context: str | os.PathLike[str], dockerfile: str | os.PathLike[str], tag: str, *,
               network: str | None = None, environ: Mapping[str, str] | None = None) -> list[str]:
    """``build -f dockerfile -t tag [--network n] [--build-arg NAME ...] context``. The proxy
    variables of ``environ`` (the process's own by default) pass through by name."""
    env = os.environ if environ is None else environ
    out = ["build", "-f", os.fspath(dockerfile), "-t", tag]
    if network:
        out += ["--network", network]
    for name in PROXY_BUILD_ARGS:
        if name in env:
            out += ["--build-arg", name]
    return [*out, os.fspath(context)]


def inspect_image_args(tag: str) -> list[str]:
    return ["image", "inspect", tag]


def kill_args(name: str) -> list[str]:
    return ["kill", name]


def failed_to_start(returncode: int | None, timed_out: bool) -> bool:
    """Whether a ``docker run`` exit means the CLI could not start the command (daemon, mount, image)."""
    return returncode == INFRA_EXIT and not timed_out
