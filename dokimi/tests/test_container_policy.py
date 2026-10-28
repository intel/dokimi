# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The defaults and opt-out branches of :class:`dokimi.container_policy.ContainerPolicy`.

The two call sites exercise one field combination each, so each field is pinned here alone.
"""

from __future__ import annotations

import pytest

from dokimi.container_policy import ContainerPolicy

_GROUPS = (
    "init_argv",
    "hardening_argv",
    "pids_argv",
    "network_argv",
    "resource_argv",
    "read_only_argv",
)


@pytest.mark.parametrize("group", _GROUPS)
def test_default_policy_adds_no_flag(group):
    assert getattr(ContainerPolicy(), group)() == []


@pytest.mark.parametrize(
    ("policy", "group", "expected"),
    [
        (ContainerPolicy(init=True), "init_argv", ["--init"]),
        (
            ContainerPolicy(no_new_privileges=True),
            "hardening_argv",
            ["--security-opt", "no-new-privileges"],
        ),
        (ContainerPolicy(cap_drop_all=True), "hardening_argv", ["--cap-drop", "ALL"]),
        (ContainerPolicy(pids_limit=64), "pids_argv", ["--pids-limit", "64"]),
        (ContainerPolicy(pids_limit=0), "pids_argv", ["--pids-limit", "0"]),
        (ContainerPolicy(network="none"), "network_argv", ["--network", "none"]),
        (ContainerPolicy(cpus=1), "resource_argv", ["--cpus", "1"]),
        (ContainerPolicy(cpus=1.5), "resource_argv", ["--cpus", "1.5"]),
        (ContainerPolicy(cpus=0.0), "resource_argv", ["--cpus", "0.0"]),
        (ContainerPolicy(memory_mb=512), "resource_argv", ["--memory", "512m"]),
        (ContainerPolicy(memory_mb=0), "resource_argv", ["--memory", "0m"]),
        (ContainerPolicy(read_only=True), "read_only_argv", ["--read-only"]),
    ],
)
def test_each_field_alone_yields_exactly_its_flag(policy, group, expected):
    assert getattr(policy, group)() == expected
    assert all(getattr(policy, g)() == [] for g in _GROUPS if g != group)


def test_hardening_flags_combine_in_order():
    policy = ContainerPolicy(no_new_privileges=True, cap_drop_all=True)
    assert policy.hardening_argv() == ["--security-opt", "no-new-privileges", "--cap-drop", "ALL"]


def test_resource_flags_combine_in_order():
    assert ContainerPolicy(cpus=2, memory_mb=256).resource_argv() == [
        "--cpus",
        "2",
        "--memory",
        "256m",
    ]
