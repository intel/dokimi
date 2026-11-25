# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The docker seam: :mod:`dokimi.docker_cmd` spells the docker CLI, and every docker argv in
the package goes through it. Nothing here starts a process: a :class:`FakeRunner` answers."""

from __future__ import annotations

import ast
import os
import signal
import sys
import threading
from pathlib import Path

import pytest

import dokimi
from dokimi import docker_cmd
from dokimi.process import FakeRunner, reply
from dokimi.process import run as run_real
from dokimi.repos import container as container_mod
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
    with pytest.raises(ContainerError, match=r"timed out after 5s \(--timeout build=SEC\)"):
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


def test_run_interrupt_kills_the_named_container(tmp_path):
    # Ctrl-C kills only the docker CLI's group; a container whose process ignores
    # SIGTERM would keep running after dokimi exits unless it is killed by name.
    def answer(call):
        if call.argv[1] == "run":
            raise KeyboardInterrupt
        return reply()

    fake = FakeRunner(answer)
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    with pytest.raises(KeyboardInterrupt):
        DockerRunner(runner=fake).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)
    assert ("docker", "kill", "dokimi-x") in fake.argvs()


def test_run_that_never_started_kills_nothing():
    fake = FakeRunner()
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", network="bogus")
    with pytest.raises(ContainerError, match="unknown network"):
        DockerRunner(runner=fake).run(spec, stdout=Path("o"), stderr=Path("e"), timeout_s=5)
    assert fake.argvs() == []


def test_run_whose_setup_fails_before_docker_starts_kills_nothing(tmp_path):
    # The named container may belong to someone else: a run that never reached docker must not kill it.
    def missing_dir(call):
        raise FileNotFoundError(call.argv[0])

    def invalid_timeout(call):
        raise ValueError("timeout must be positive")

    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    for answer, timeout_s, stdout in [(missing_dir, 5, tmp_path / "gone" / "o"), (invalid_timeout, 0, tmp_path / "o")]:
        fake = FakeRunner(answer)
        with pytest.raises((FileNotFoundError, ValueError)):
            DockerRunner(runner=fake).run(spec, stdout=stdout, stderr=tmp_path / "e", timeout_s=timeout_s)
        assert ("docker", "kill", "dokimi-x") not in fake.argvs()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_interrupt_while_stdout_waits_for_a_reader_kills_nothing(tmp_path):
    # Opening a FIFO with no reader blocks before docker is started: a Ctrl-C there must not kill the name.
    calls: list[list[str]] = []

    def recording(argv, **kwargs):
        calls.append(list(argv))
        return run_real(argv, **kwargs)

    fifo = tmp_path / "o"
    os.mkfifo(fifo)
    # The delay lets the main thread reach the blocking open(); nothing shows that it has.
    threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGINT)).start()  # timing-ok: no event marks the blocked open
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    with pytest.raises(KeyboardInterrupt):
        DockerRunner(docker="/nonexistent/docker", runner=recording).run(spec, stdout=fifo, stderr=tmp_path / "e",
                                                                        timeout_s=5)
    assert not any(argv[1:2] == ["kill"] for argv in calls), calls


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_a_fifo_reader_gets_no_eof_before_docker_starts(tmp_path):
    # The writer opened before the run must stay open: closing it would send the reader EOF before docker starts.
    fifo = tmp_path / "o"
    os.mkfifo(fifo)
    reader_done, eof_before_start = threading.Event(), []

    def read():
        with open(fifo, "rb") as stream:
            stream.read()
        reader_done.set()

    def answer(call):
        if call.argv[1] == "run":
            eof_before_start.append(reader_done.wait(0.5))
        return reply()

    reader = threading.Thread(target=read)
    reader.start()
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    DockerRunner(runner=FakeRunner(answer)).run(spec, stdout=fifo, stderr=tmp_path / "e", timeout_s=5)
    reader.join(5)
    assert eof_before_start == [False]
    assert reader_done.is_set()


def test_a_container_a_halt_killed_is_no_result(tmp_path):
    # The batch's cleanup kills a grading container and it returns rc 137: that exit is no result to record.
    def answer(call):
        if call.argv[1] == "run":
            container_mod.halt_and_kill_started()
            return reply(returncode=137)
        return reply()

    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
    try:
        with pytest.raises(container_mod.BatchInterrupted):
            DockerRunner(runner=FakeRunner(answer)).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e",
                                                        timeout_s=5)
    finally:
        container_mod.clear_halt()


def test_a_grading_container_is_neither_killed_nor_refused_by_a_halt(tmp_path):
    # Ctrl-C lets a cell past its agent finish: its grading container runs on, and it may start after the halt.
    kills = []

    def answer(call):
        if call.argv[1] == "kill":
            kills.append(call.argv)
        if call.argv[1] == "run":
            container_mod.halt_and_kill_started()  # the Ctrl-C lands while the grade runs
            return reply(returncode=0)
        return reply()

    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-grade", survives_halt=True)
    try:
        result = DockerRunner(runner=FakeRunner(answer)).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e",
                                                             timeout_s=5)
        assert result.returncode == 0 and kills == []
        container_mod.halt_and_kill_started()  # halted: an agent is still refused, a grade may start
        grade = ContainerSpec(image="img", argv=["true"], name="dokimi-grade2", survives_halt=True)
        assert DockerRunner(runner=FakeRunner(lambda call: reply())).run(
            grade, stdout=tmp_path / "o2", stderr=tmp_path / "e2", timeout_s=5).returncode == 0
        with pytest.raises(container_mod.BatchInterrupted):
            DockerRunner(runner=FakeRunner(lambda call: reply())).run(
                ContainerSpec(image="img", argv=["true"], name="dokimi-agent"), stdout=tmp_path / "o3",
                stderr=tmp_path / "e3", timeout_s=5)
    finally:
        container_mod.clear_halt()


def test_run_with_a_missing_mount_kills_nothing(tmp_path):
    fake = FakeRunner()
    spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000",
                         mounts=[Mount(tmp_path / "absent", "/eval/run")])
    with pytest.raises(ContainerError, match="mount source does not exist"):
        DockerRunner(runner=fake).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)
    assert fake.argvs() == []


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


@pytest.mark.skipif(not hasattr(os, "mkfifo") or sys.platform == "win32", reason="needs a FIFO and a POSIX shell")
def test_a_fifo_output_with_a_reader_finishes_when_docker_exits(tmp_path):
    # The run holds the FIFO's writers through docker's start. Reading the FIFO back while they are open waits for
    # an EOF that only the run's return sends, so a real run must not hang here (the fake runner above never reads).
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\necho started\n", encoding="utf-8")
    fake.chmod(0o755)
    fifo = tmp_path / "o"
    os.mkfifo(fifo)
    got, done = [], []

    def read():
        with open(fifo, "rb") as stream:
            got.append(stream.read())

    def run():
        spec = ContainerSpec(image="img", argv=["true"], name="dokimi-x", user="1000:1000")
        done.append(DockerRunner(docker=str(fake), runner=run_real).run(spec, stdout=fifo, stderr=tmp_path / "e",
                                                                        timeout_s=10))

    reader = threading.Thread(target=read, daemon=True)
    worker = threading.Thread(target=run, daemon=True)
    reader.start()
    worker.start()
    worker.join(20)
    assert done, "a run with a FIFO output and a reader did not finish after docker exited"
    reader.join(5)
    assert got == [b"started\n"]
