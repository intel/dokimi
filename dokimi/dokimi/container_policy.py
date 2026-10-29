# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The hardening and resource flags of a ``docker run``, spelled once.

Two call sites start containers with the docker CLI: :class:`dokimi.repos.container.DockerRunner`
and the docker backend of :mod:`dokimi.metrics.example_runner`. They use different subsets of
the same flags, and each has its own argv order. :class:`ContainerPolicy` owns how every flag
is spelled. A call site sets the fields it wants and splices the groups in the order its argv
has always had; that order is pinned by ``tests/test_container_argv_characterization.py``.

The builder chooses nothing and validates nothing: a field left at its default adds no flag,
so each call site opts in to what it runs with.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContainerPolicy:
    network: str | None = None  # --network; None adds no flag
    init: bool = False  # --init: a daemonizing build server is reaped, its cache writes are kept
    no_new_privileges: bool = False  # --security-opt no-new-privileges: a setuid binary cannot gain root
    cap_drop_all: bool = False  # --cap-drop ALL
    pids_limit: int | None = None  # --pids-limit: a ceiling against a fork bomb
    cpus: float | None = None  # --cpus
    memory_mb: int | None = None  # --memory <n>m
    read_only: bool = False  # --read-only: the container's root filesystem

    def init_argv(self) -> list[str]:
        return ["--init"] if self.init else []

    def hardening_argv(self) -> list[str]:
        out: list[str] = []
        if self.no_new_privileges:
            out += ["--security-opt", "no-new-privileges"]
        if self.cap_drop_all:
            out += ["--cap-drop", "ALL"]
        return out

    def pids_argv(self) -> list[str]:
        return [] if self.pids_limit is None else ["--pids-limit", str(self.pids_limit)]

    def network_argv(self) -> list[str]:
        return [] if self.network is None else ["--network", self.network]

    def resource_argv(self) -> list[str]:
        out: list[str] = []
        if self.cpus is not None:
            out += ["--cpus", str(self.cpus)]
        if self.memory_mb is not None:
            out += ["--memory", f"{self.memory_mb}m"]
        return out

    def read_only_argv(self) -> list[str]:
        return ["--read-only"] if self.read_only else []
