# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The environment the docker-* harnesses give a task's oracle and verifier steps."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from dokimi import docker_cmd

from .network_policy import PROXY_VARS as _PROXY_VARS, _USERINFO_RE, PhasePolicy, proxy_env, resolve  # noqa: F401
from .tasks import TaskSpec


def _exec_env(task: TaskSpec, table: str, net: Optional[PhasePolicy] = None, *,
              credentials: bool = True) -> tuple[list[str], dict[str, str]]:
    """The literal environment of the oracle or verifier step, and its proxy variables.

    Returns ``(flags, proxy)``: ``flags`` are the ``-e KEY=value`` pairs of the task's literal
    ``[verifier.env]`` / ``[oracle.env]``; ``proxy`` maps each proxy variable to its value. The
    proxy values never go on an argv (every user of the host can read one): :func:`exec_env_file`
    writes them to a 0600 env file. The literal values stay on the argv, as before.

    A public-network task gets the host's proxy variables: behind a corporate
    proxy a verifier that installs ``uv`` or pytest at verify time (52 of 101
    SkillsBench ``test.sh`` install both, 90 install something) otherwise fails
    with reward 0 (measured on ``parallel-tfidf-search``). They go on the exec,
    not on ``docker run``, so they reach only the steps the task's
    ``network_mode`` is about. With ``credentials=False`` (the verifier after
    an in-container agent, which runs the agent's code) their ``user:pw@`` is
    dropped: the egress sidecar holds the credentials so the agent never sees
    them, and a planted ``conftest.py`` must not read them at verify time.

    Then the task's ``[verifier.env]`` / ``[oracle.env]``: only literal values
    are forwarded; BenchFlow's ``${VAR}`` host references are left to
    BenchFlow, so a secret is never read from our environment on a task
    file's say-so.
    """
    if net is None:  # the step's policy from the task alone (see .network_policy)
        plan = resolve(task)
        net = plan.verify if table == "verifier" else plan.solve
    proxy = proxy_env(net, credentials=credentials)
    flags: list[str] = []
    source = task.verifier if table == "verifier" else task.oracle
    env = source.get("env")
    if not isinstance(env, dict):
        return flags, proxy
    for key, value in env.items():
        if isinstance(value, bool):
            flags += ["-e", f"{key}={'true' if value else 'false'}"]  # YAML true, not Python True
        elif isinstance(value, (str, int, float)) and "${" not in str(value):
            flags += ["-e", f"{key}={value}"]
    return flags, proxy


@contextmanager
def exec_env_file(task: TaskSpec, table: str, net: Optional[PhasePolicy] = None, *,
                  credentials: bool = True) -> Iterator[tuple[list[str], dict[str, str]]]:
    """The ``docker exec`` flags and the docker CLI's environment for the oracle or verifier step.

    The proxy values go in a 0600 ``--env-file`` in a scratch directory that is removed on exit;
    the argv carries ``--env-file <path>`` and the literal ``-e`` pairs. The client's environment
    is the operator's own, so the step's proxy cannot change the route of the docker CLI (a tcp
    ``DOCKER_HOST``); only a value with a newline or carriage return travels as ``-e NAME`` with the client's env.
    """
    literal, proxy = _exec_env(task, table, net, credentials=credentials)
    with tempfile.TemporaryDirectory(prefix="dokimi-env-") as scratch:
        name, multiline = docker_cmd.write_env_file(Path(scratch), proxy)
        flags = ["--env-file", name, *(a for key in multiline for a in ("-e", key)), *literal]
        yield flags, multiline


def unresolved_env(task: TaskSpec, table: str) -> list[str]:
    """Names in ``[verifier.env]`` / ``[oracle.env]`` that :func:`_exec_env`
    does not forward: a ``${VAR}`` host reference (with or without a default),
    or a value that is not a scalar (list, table, null)."""
    source = task.verifier if table == "verifier" else task.oracle
    env = source.get("env")
    if not isinstance(env, dict):
        return []
    return sorted(
        str(k) for k, v in env.items()
        if not isinstance(v, (str, int, float)) or (isinstance(v, str) and "${" in v)
    )
