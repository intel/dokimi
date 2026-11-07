# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The docker seam: :mod:`dokimi.docker_cmd` spells the docker CLI, and every docker argv in
the package goes through it. Nothing here starts a process: a :class:`FakeRunner` answers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import dokimi
from dokimi import docker_cmd
from dokimi.process import FakeRunner, reply
from dokimi.repos.container import ContainerError, ContainerSpec, DockerRunner, Mount


def test_argv_is_the_binary_then_the_args():
    assert docker_cmd.argv("ps", "-a") == ["docker", "ps", "-a"]


def test_mount_args_are_bind_mounts_with_readonly_only_when_asked():
    assert docker_cmd.mount_args([("/h", "/c", False), ("/h2", "/c2", True)]) == [
        "--mount", "type=bind,src=/h,dst=/c",
        "--mount", "type=bind,src=/h2,dst=/c2,readonly",
    ]


def test_mount_args_refuse_a_comma_in_either_path():
    with pytest.raises(ValueError, match="cannot contain ','"):
        docker_cmd.mount_args([("/a,b", "/c", False)])
    with pytest.raises(ValueError, match="cannot contain ','"):
        docker_cmd.mount_args([("/a", "/c,d", False)])


def test_env_args_set_values_then_names_passed_through():
    assert docker_cmd.env_args({"A": "1"}, ["TOKEN"]) == ["-e", "A=1", "-e", "TOKEN"]


def test_build_args_pass_only_the_proxy_variables_that_are_set():
    args = docker_cmd.build_args("/ctx", "/ctx/Dockerfile", "t:1", network="host",
                                 environ={"https_proxy": "http://p:3128", "PATH": "/bin"})
    assert args == ["build", "-f", "/ctx/Dockerfile", "-t", "t:1", "--network", "host",
                    "--build-arg", "https_proxy", "/ctx"]


def test_build_args_pass_every_proxy_variable_in_order(monkeypatch):
    for name in docker_cmd.PROXY_BUILD_ARGS:
        monkeypatch.setenv(name, "http://p:3128")
    args = docker_cmd.build_args("/ctx", "/ctx/Dockerfile", "t:1")
    expected = [part for name in docker_cmd.PROXY_BUILD_ARGS for part in ("--build-arg", name)]
    assert args == ["build", "-f", "/ctx/Dockerfile", "-t", "t:1", *expected, "/ctx"]


def test_failed_to_start_is_the_infra_exit_and_not_a_timeout():
    assert docker_cmd.failed_to_start(125, False)
    assert not docker_cmd.failed_to_start(125, True)
    assert not docker_cmd.failed_to_start(1, False)


def test_build_timeout_names_the_flag(tmp_path):
    fake = FakeRunner(lambda call: reply(stderr="stuck", timed_out=True))
    with pytest.raises(ContainerError, match=r"timed out after 5s \(--build-timeout\)"):
        DockerRunner(runner=fake).build(tmp_path, tmp_path / "Dockerfile", "t:1", timeout_s=5)
    assert fake.calls[0].timeout == 5


def test_build_failure_carries_the_tail_of_the_output(tmp_path):
    fake = FakeRunner(lambda call: reply(returncode=1, stderr="RUN step broke"))
    with pytest.raises(ContainerError, match="failed:\nRUN step broke"):
        DockerRunner(runner=fake).build(tmp_path, tmp_path / "Dockerfile", "t:1")


def test_build_with_no_docker_binary_is_a_container_error(tmp_path):
    def missing(call):
        raise OSError("no such file")

    with pytest.raises(ContainerError, match="cannot run 'docker'"):
        DockerRunner(runner=FakeRunner(missing)).build(tmp_path, tmp_path / "Dockerfile", "t:1")


def test_image_exists_follows_the_inspect_exit_code():
    assert DockerRunner(runner=FakeRunner(lambda c: reply())).image_exists("t:1") is True
    assert DockerRunner(runner=FakeRunner(lambda c: reply(returncode=1))).image_exists("t:1") is False


def test_image_inspect_that_never_answers_is_a_container_error():
    fake = FakeRunner(lambda call: reply(timed_out=True))
    with pytest.raises(ContainerError, match="no answer"):
        DockerRunner(runner=fake).image_exists("t:1")


def test_run_refuses_a_mount_source_that_does_not_exist(tmp_path):
    spec = ContainerSpec(image="img", argv=["true"], mounts=[Mount(tmp_path / "missing", "/eval/run")])
    with pytest.raises(ContainerError, match="mount source does not exist"):
        DockerRunner(runner=FakeRunner()).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)


def test_run_timeout_kills_the_named_container(tmp_path):
    def answer(call):
        return reply(timed_out=True) if call.argv[1] == "run" else reply()

    fake = FakeRunner(answer)
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    result = DockerRunner(runner=fake).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)
    assert result.timed_out and result.returncode is None
    assert ("docker", "kill", "dokimi-x") in fake.argvs()


def test_docker_runner_argv_refuses_a_comma_as_a_container_error():
    spec = ContainerSpec(image="img", argv=["true"], mounts=[Mount(Path("/x,y"), "/eval/run")])
    with pytest.raises(ContainerError, match="cannot contain ','"):
        DockerRunner(runner=FakeRunner()).argv(spec)


def test_no_docker_argv_is_spelled_outside_the_module():
    """Every ``docker`` argv is built by :mod:`dokimi.docker_cmd`: a list or a tuple that starts
    with the literal ``"docker"`` anywhere else in the package is a second spelling."""
    package = Path(dokimi.__file__).parent
    spelled = []
    for path in sorted(package.rglob("*.py")):
        if path.name == "docker_cmd.py" and path.parent == package:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "docker":
                    spelled.append(f"{path.relative_to(package)}:{node.lineno}")
    assert not spelled, f"spell docker through dokimi.docker_cmd.argv: {spelled}"
