# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The environment the docker-* harnesses give a task's oracle and verifier steps."""

from __future__ import annotations

from typing import Optional

from .network_policy import PROXY_VARS as _PROXY_VARS, _USERINFO_RE, PhasePolicy, proxy_env_flags, resolve  # noqa: F401
from .tasks import TaskSpec


def _exec_env(task: TaskSpec, table: str, net: Optional[PhasePolicy] = None, *,
              credentials: bool = True) -> list[str]:
    """``docker exec -e`` flags for the oracle or verifier step.

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
    flags = proxy_env_flags(net, credentials=credentials)
    source = task.verifier if table == "verifier" else task.oracle
    env = source.get("env")
    if not isinstance(env, dict):
        return flags
    for key, value in env.items():
        if isinstance(value, bool):
            flags += ["-e", f"{key}={'true' if value else 'false'}"]  # YAML true, not Python True
        elif isinstance(value, (str, int, float)) and "${" not in str(value):
            flags += ["-e", f"{key}={value}"]
    return flags


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
