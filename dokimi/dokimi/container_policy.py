# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The hardening and resource flags of a ``docker run``, spelled once.

Every ``docker run`` the package builds takes its ``--network``, hardening, resource and ``--init``
flags from :class:`ContainerPolicy`: the repo container (:class:`dokimi.repos.container.DockerRunner`),
the example runner's docker backend, the solver's task container and the fidelity capture's client.
The egress sidecar's ``docker create`` still spells its ``--network`` inline. Each call site sets the
fields it wants and lays the groups out with :func:`dokimi.docker_cmd.run_spec`, in the order its argv
has always had; that order is pinned by ``tests/test_container_argv_characterization.py`` and
``tests/test_docker_run_argv_differential.py``.

This module also holds the task container's defaults (:data:`PIDS_LIMIT`, :data:`TASK_CAP_ADD`,
:func:`task_container_policy`). The policy itself chooses nothing and validates nothing: a field
left at its default adds no flag, so each call site opts in to what it runs with.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

PIDS_LIMIT = 32768  # threads count too: a Bazel server and a -j build on a many-core host use thousands
# What a task container keeps of root's capabilities after ``--cap-drop ALL``: chown, DAC override,
# file owner, and setuid/setgid for task scripts that drop privileges.
TASK_CAP_ADD = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID")
# The flag groups :meth:`ContainerPolicy.group` names; a layout item is one of these or an argv list.
Group = Literal["init", "security", "cap_drop", "hardening", "pids", "cap_add", "network", "resource",
                "read_only"]


@dataclass(frozen=True)
class ContainerPolicy:
    network: str | None = None  # --network; None adds no flag
    init: bool = False  # --init: a daemonizing build server is reaped, its cache writes are kept
    no_new_privileges: bool = False  # --security-opt no-new-privileges: a setuid binary cannot gain root
    cap_drop_all: bool = False  # --cap-drop ALL
    cap_add: tuple[str, ...] = ()  # --cap-add per name: a capability the container's own steps need back
    pids_limit: int | None = None  # --pids-limit: a ceiling against a fork bomb
    cpus: float | str | None = None  # --cpus, spelled as given
    memory: str | None = None  # --memory, as docker spells it ("512m", "2g")
    read_only: bool = False  # --read-only: the container's root filesystem

    def group(self, name: Group) -> list[str]:
        """The argv of one flag group by name: ``init``, ``security``, ``cap_drop``, ``hardening``
        (security then cap drop), ``pids``, ``cap_add``, ``network``, ``resource``, ``read_only``.
        An unknown name is a ``KeyError``."""
        groups = {
            "init": self.init_argv, "security": self.security_argv, "cap_drop": self.cap_drop_argv,
            "hardening": self.hardening_argv, "pids": self.pids_argv, "cap_add": self.cap_add_argv,
            "network": self.network_argv, "resource": self.resource_argv, "read_only": self.read_only_argv,
        }
        return groups[name]()

    def init_argv(self) -> list[str]:
        return ["--init"] if self.init else []

    def security_argv(self) -> list[str]:
        return ["--security-opt", "no-new-privileges"] if self.no_new_privileges else []

    def cap_drop_argv(self) -> list[str]:
        return ["--cap-drop", "ALL"] if self.cap_drop_all else []

    def hardening_argv(self) -> list[str]:
        return [*self.security_argv(), *self.cap_drop_argv()]

    def cap_add_argv(self) -> list[str]:
        return [flag for cap in self.cap_add for flag in ("--cap-add", cap)]

    def pids_argv(self) -> list[str]:
        return [] if self.pids_limit is None else ["--pids-limit", str(self.pids_limit)]

    def network_argv(self) -> list[str]:
        return [] if self.network is None else ["--network", self.network]

    def resource_argv(self) -> list[str]:
        out: list[str] = []
        if self.cpus is not None:
            out += ["--cpus", str(self.cpus)]
        if self.memory is not None:
            out += ["--memory", self.memory]
        return out

    def read_only_argv(self) -> list[str]:
        return ["--read-only"] if self.read_only else []


def repo_container_policy(network: str | None) -> ContainerPolicy:
    """The repo container's hardening: ``--init``, ``--cap-drop ALL``, no-new-privileges, a pids limit."""
    return ContainerPolicy(network=network, init=True, no_new_privileges=True, cap_drop_all=True,
                           pids_limit=PIDS_LIMIT)


def task_container_policy(*, network: str | None, cpus: str, memory: str) -> ContainerPolicy:
    """The repo container's policy with :data:`TASK_CAP_ADD` back for the container's own steps, and the
    task's cpus and memory."""
    return replace(repo_container_policy(network), cap_add=TASK_CAP_ADD, cpus=cpus, memory=memory)
