# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The defaults and opt-out branches of :class:`dokimi.container_policy.ContainerPolicy`.

The two call sites exercise one field combination each, so each field is pinned here alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.container_policy import ContainerPolicy

_GROUPS = (
    "init_argv",
    "hardening_argv",
    "cap_add_argv",
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
        (ContainerPolicy(cap_add=("CHOWN", "SETUID")), "cap_add_argv",
         ["--cap-add", "CHOWN", "--cap-add", "SETUID"]),
        (ContainerPolicy(pids_limit=64), "pids_argv", ["--pids-limit", "64"]),
        (ContainerPolicy(pids_limit=0), "pids_argv", ["--pids-limit", "0"]),
        (ContainerPolicy(network="none"), "network_argv", ["--network", "none"]),
        (ContainerPolicy(cpus=1), "resource_argv", ["--cpus", "1"]),
        (ContainerPolicy(cpus=1.5), "resource_argv", ["--cpus", "1.5"]),
        (ContainerPolicy(cpus=0.0), "resource_argv", ["--cpus", "0.0"]),
        (ContainerPolicy(memory="512m"), "resource_argv", ["--memory", "512m"]),
        (ContainerPolicy(cpus="3"), "resource_argv", ["--cpus", "3"]),
        (ContainerPolicy(cpus=0), "resource_argv", ["--cpus", "0"]),
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
    assert ContainerPolicy(cpus=2, memory="256m").resource_argv() == [
        "--cpus",
        "2",
        "--memory",
        "256m",
    ]


def test_security_and_cap_drop_are_separate_groups_in_hardening_order():
    policy = ContainerPolicy(no_new_privileges=True, cap_drop_all=True)
    assert policy.security_argv() == ["--security-opt", "no-new-privileges"]
    assert policy.cap_drop_argv() == ["--cap-drop", "ALL"]
    assert policy.hardening_argv() == [*policy.security_argv(), *policy.cap_drop_argv()]


def test_group_by_name_is_the_named_argv():
    policy = ContainerPolicy(init=True, cap_drop_all=True, pids_limit=7, network="none", read_only=True)
    for name in ("init", "security", "cap_drop", "hardening", "pids", "cap_add", "network", "resource", "read_only"):
        assert policy.group(name) == getattr(policy, f"{name}_argv")()


def test_unknown_group_name_is_refused():
    with pytest.raises(KeyError):
        ContainerPolicy().group("entrypoint")


def test_group_names_are_one_literal_type():
    # a typo in a layout is a mypy error, not a KeyError on the path that runs it
    from typing import get_args

    from dokimi.container_policy import Group

    assert set(get_args(Group)) == {"init", "security", "cap_drop", "hardening", "pids", "cap_add",
                                    "network", "resource", "read_only"}


def test_docs_do_not_name_a_removed_symbol():
    # test_docs_code_refs.py checks code spans in docs, not a bare name in prose, so this checks the prose
    root = Path(__file__).resolve().parents[1]
    stale = [p.relative_to(root).as_posix() for p in (root / "docs").rglob("*.md")
             if "TASK_CONTAINER_HARDENING" in p.read_text(encoding="utf-8")]
    assert stale == []
