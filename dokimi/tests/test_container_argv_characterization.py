# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Characterization of the ``docker run`` argv each call site builds today.

These pin the exact argv, flag order included, of the two container call sites that
the container policy builder serves: :class:`dokimi.repos.container.DockerRunner` and
the docker backend of :mod:`dokimi.metrics.example_runner`. They were written against
the code before the builder existed, and must keep passing through the refactor: a
change to any expected list here is an argv change, which is a separate decision.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.metrics.example_runner import EXECUTORS, ExecutionPolicy, run_example
from dokimi.process import FakeRunner, reply
from dokimi.repos import container
from dokimi.repos.container import (
    CONTENT_DIR,
    WORKSPACE,
    ContainerError,
    ContainerSpec,
    DockerRunner,
    Mount,
)

# --- repos/container.py: DockerRunner.argv -----------------------------------------


def _no_host_user(monkeypatch: pytest.MonkeyPatch, uid_gid: str | None) -> None:
    monkeypatch.setattr(container, "host_user", lambda: uid_gid)


def test_repo_argv_full_spec_pins_every_flag_in_order(monkeypatch):
    _no_host_user(monkeypatch, None)
    spec = ContainerSpec(
        image="img",
        argv=["python3", "x"],
        mounts=[Mount(Path("/h/run"), "/eval/run"), Mount(Path("/h/content"), CONTENT_DIR, read_only=True)],
        env={"HOME": "/eval/run/home"},
        pass_env=("AWS_BEARER_TOKEN_BEDROCK",),
        network="none",
        name="dokimi-x",
        user="1000:1000",
    )
    assert DockerRunner().argv(spec) == [
        "docker", "run", "--rm", "--init",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--pids-limit", "32768",
        "--network", "none",
        "-w", WORKSPACE,
        "--name", "dokimi-x",
        "--user", "1000:1000",
        "--mount", "type=bind,src=/h/run,dst=/eval/run",
        "--mount", f"type=bind,src=/h/content,dst={CONTENT_DIR},readonly",
        "-e", "HOME=/eval/run/home",
        "-e", "AWS_BEARER_TOKEN_BEDROCK",
        "--entrypoint", "", "img", "python3", "x",
    ]


@pytest.mark.parametrize("network", ["none", "bridge", "host"])
def test_repo_argv_network_mode_is_the_only_network_difference(monkeypatch, network):
    _no_host_user(monkeypatch, None)
    argv = DockerRunner().argv(ContainerSpec(image="img", argv=["make"], network=network))
    assert argv == [
        "docker", "run", "--rm", "--init",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--pids-limit", "32768",
        "--network", network,
        "-w", WORKSPACE,
        "--entrypoint", "", "img", "make",
    ]


def test_repo_argv_takes_the_host_uid_gid_when_the_spec_has_no_user(monkeypatch):
    _no_host_user(monkeypatch, "4242:4343")
    argv = DockerRunner().argv(ContainerSpec(image="img", argv=["true"], network="none"))
    i = argv.index("--user")
    assert argv[i - 2:i + 2] == ["-w", WORKSPACE, "--user", "4242:4343"]


def test_repo_argv_omits_user_when_there_is_no_posix_uid(monkeypatch):
    _no_host_user(monkeypatch, None)
    assert "--user" not in DockerRunner().argv(ContainerSpec(image="img", argv=["true"]))


def test_repo_argv_unknown_network_is_refused():
    with pytest.raises(ContainerError, match="unknown network 'wide-open'"):
        DockerRunner().argv(ContainerSpec(image="i", argv=[], network="wide-open"))


def test_repo_argv_writable_mount_has_no_readonly_suffix(monkeypatch):
    _no_host_user(monkeypatch, None)
    argv = DockerRunner().argv(ContainerSpec(image="i", argv=[], mounts=[Mount(Path("/h/w"), "/eval/w")]))
    assert "type=bind,src=/h/w,dst=/eval/w" in argv
    assert "readonly" not in " ".join(argv)


# --- metrics/example_runner.py: docker backend argv ------------------------------

_TMP_MOUNT = "<tmp>:/work:ro"


def _docker_cmd(policy: ExecutionPolicy, lang: str = "python") -> tuple[list[str], float]:
    """The argv and timeout of one docker-backend run, with the per-run temp dir masked."""
    runner = FakeRunner(lambda call: reply())
    res = run_example(lang, "print(1)", policy, runner)
    assert res.passed is True
    (call,) = runner.calls
    argv = [_TMP_MOUNT if a.endswith(":/work:ro") else a for a in call.argv]
    return argv, call.timeout


def test_example_docker_argv_default_policy_pins_every_flag_in_order():
    argv, timeout = _docker_cmd(ExecutionPolicy(backend="docker", timeout=7))
    assert argv == [
        "docker", "run", "--rm",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--network", "none",
        "--cpus", "1.0",
        "--memory", "512m",
        "--pids-limit", "64",
        "--read-only",
        "-v", _TMP_MOUNT,
        "python:3.11-slim",
        "timeout", "7",
        *EXECUTORS["python"][1], "/work/snippet.py",
    ]
    assert timeout == 37  # the snippet's timeout plus the docker CLI's grace


def test_example_docker_argv_resource_values_come_from_the_policy():
    policy = ExecutionPolicy(backend="docker", timeout=3, docker_image="repo/img:1", mem_mb=1024, cpus=2.5)
    argv, _ = _docker_cmd(policy, lang="bash")
    assert argv == [
        "docker", "run", "--rm",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--network", "none",
        "--cpus", "2.5",
        "--memory", "1024m",
        "--pids-limit", "64",
        "--read-only",
        "-v", _TMP_MOUNT,
        "repo/img:1",
        "timeout", "3",
        *EXECUTORS["bash"][1], "/work/snippet.sh",
    ]


def test_example_docker_argv_never_sets_network_from_the_policy():
    # The docker backend has no network mode: it is always "none".
    argv, _ = _docker_cmd(ExecutionPolicy(backend="docker", timeout=5))
    assert argv[argv.index("--network") + 1] == "none"


@pytest.mark.parametrize("lang", sorted(EXECUTORS))
def test_example_docker_argv_drops_every_capability_and_new_privileges(lang):
    # The snippet runs as the image's user, root in the default image: it keeps no capability
    # and a setuid binary gives it none, as in repos/container.py.
    argv, _ = _docker_cmd(ExecutionPolicy(backend="docker", timeout=5), lang=lang)
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
