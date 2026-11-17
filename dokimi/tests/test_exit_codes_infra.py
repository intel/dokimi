# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit code 5 (infra_error) of issue #94 §2.6: an uncaught exception exits 5 and prints its traceback."""

from __future__ import annotations

import argparse

import pytest

from dokimi import cli


def test_an_uncaught_exception_exits_5_and_prints_its_traceback(monkeypatch, capsys):
    def crash(_args):
        raise RuntimeError("harness crashed")

    monkeypatch.setattr(cli, "parse_args", lambda: argparse.Namespace(cmd="run", func=crash))
    monkeypatch.setattr(cli.process, "install_signal_handlers", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 5  # not 1: a crash never reads as a failed verdict
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: harness crashed" in err


def test_a_docker_cli_that_cannot_start_is_a_docker_error_and_a_failed_build_is_not(tmp_path):
    """Docker failures are infrastructure (exit 5 in `repo run`); a build of the manifest's image that fails is config."""
    from types import SimpleNamespace

    from dokimi.repos.container import ContainerError, DockerError, DockerRunner

    def missing_cli(argv, **_):
        raise OSError("No such file or directory")

    def failed_build(argv, **_):
        return SimpleNamespace(timed_out=False, returncode=1, stderr="RUN false", stdout="")

    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(DockerError):
        DockerRunner(docker="docker-missing", runner=missing_cli).build(tmp_path, tmp_path / "Dockerfile", "t")
    with pytest.raises(ContainerError) as exc:
        DockerRunner(runner=failed_build).build(tmp_path, tmp_path / "Dockerfile", "t")
    assert not isinstance(exc.value, DockerError)


DAEMON_DOWN = ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n")


def test_a_daemon_that_is_down_is_a_docker_error_before_any_build():
    """`repo run` asks for the image first: with the daemon down that answer is infra (exit 5), not a missing image."""
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    fake = process.FakeRunner(lambda call: process.reply(stderr=DAEMON_DOWN, returncode=1))
    with pytest.raises(DockerError, match="Cannot connect to the Docker daemon"):
        DockerRunner(runner=fake).image_exists("img:t")
    assert [c.argv[1] for c in fake.calls] == ["image"]  # the build is never started


def test_an_older_cli_got_permission_denied_on_the_socket_is_a_docker_error(tmp_path):
    """Older docker CLIs open the socket error with `Got permission denied`: no permission on the socket is infra."""
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    got = ("Got permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: "
           "Post \"http://%2Fvar%2Frun%2Fdocker.sock/v1.24/build\": dial unix /var/run/docker.sock: permission denied\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fake = process.FakeRunner(lambda call: process.reply(stderr=got, returncode=1))
    with pytest.raises(DockerError):
        DockerRunner(runner=fake).build(tmp_path, tmp_path / "Dockerfile", "t")


NEWER_CLI_DAEMON_DOWN = (
    "failed to connect to the docker API at unix:///var/run/docker.sock. Check if the path is correct and if the "
    "daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory\n"
)


def test_a_newer_cli_daemon_diagnostic_is_a_docker_error_on_inspect_and_on_build(tmp_path):
    """Docker CLIs since 2025 say `failed to connect to the docker API`: that is the daemon down (exit 5), not a
    missing image and not a failed build (exit 1)."""
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    inspect = process.FakeRunner(lambda call: process.reply(stderr=NEWER_CLI_DAEMON_DOWN, returncode=1))
    with pytest.raises(DockerError, match="failed to connect to the docker API"):
        DockerRunner(runner=inspect).image_exists("img:t")
    build = process.FakeRunner(lambda call: process.reply(stderr=NEWER_CLI_DAEMON_DOWN, returncode=1))
    with pytest.raises(DockerError):
        DockerRunner(runner=build).build(tmp_path, tmp_path / "Dockerfile", "t")


BUILDKIT_QUOTED_MESSAGE = (
    "#5 [3/4] RUN echo 'Cannot connect to the Docker daemon' && false\n"
    "#5 0.1 Cannot connect to the Docker daemon\n"
    "ERROR: failed to solve: process \"/bin/sh -c echo 'Cannot connect to the Docker daemon' && false\" "
    "did not complete successfully: exit code: 1\n"
)


def test_a_build_step_that_quotes_the_daemon_message_is_a_container_error_not_infra(tmp_path):
    """A RUN step whose command text names the daemon message is a failed manifest build (exit 1), not a daemon
    that is down (exit 5): only the CLI's own top-level diagnostic counts."""
    from dokimi import process
    from dokimi.repos.container import ContainerError, DockerError, DockerRunner

    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    quoted = process.FakeRunner(lambda call: process.reply(stderr=BUILDKIT_QUOTED_MESSAGE, returncode=1))
    with pytest.raises(ContainerError) as exc:
        DockerRunner(runner=quoted).build(tmp_path, tmp_path / "Dockerfile", "t")
    assert not isinstance(exc.value, DockerError)
    down = process.FakeRunner(lambda call: process.reply(stderr=DAEMON_DOWN, returncode=1))
    with pytest.raises(DockerError):
        DockerRunner(runner=down).build(tmp_path, tmp_path / "Dockerfile", "t")


def test_a_docker_cli_missing_on_the_image_inspect_is_a_docker_error():
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    def missing_cli(call):
        raise OSError("No such file or directory")

    with pytest.raises(DockerError, match="cannot run"):
        DockerRunner(docker="docker-missing", runner=process.FakeRunner(missing_cli)).image_exists("img:t")


def test_no_such_image_is_still_a_plain_miss_not_an_infra_error():
    from dokimi import process
    from dokimi.repos.container import DockerRunner

    no_image = process.reply(stderr="Error: No such image: img:t\n", returncode=1)
    assert DockerRunner(runner=process.FakeRunner(lambda call: no_image)).image_exists("img:t") is False


def test_a_build_with_the_daemon_down_is_a_docker_error(tmp_path):
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    fake = process.FakeRunner(lambda call: process.reply(stderr=DAEMON_DOWN, returncode=1))
    with pytest.raises(DockerError):
        DockerRunner(runner=fake).build(tmp_path, tmp_path / "Dockerfile", "t")


def test_a_refused_cell_is_exit_1_and_a_machinery_failure_is_exit_5():
    """A cell refused on its inputs (a malformed MCP config) is an ordinary error; a docker or file failure is infra."""
    from dokimi.commands import repo_run
    from dokimi.repos.errors import PrepError
    from dokimi.repos.container import DockerError
    from dokimi.repos.runner import _is_infra

    assert _is_infra(DockerError("docker run failed")) and _is_infra(OSError("scratch vanished"))
    assert not _is_infra(PrepError("malformed .mcp.json"))

    def exit_of(err: dict) -> int:
        summary = {"errors": [err], "budget_stopped": 0, "probe_failed": [], "ran": 0, "skipped": 0,
                   "unsupported": 0, "cells": 1}
        with pytest.raises(SystemExit) as exc:
            repo_run._exit_of_batch(summary)
        return exc.value.code

    assert exit_of({"cell": "c", "error": "x", "infra": True}) == 5
    assert exit_of({"cell": "c", "error": "x", "infra": False}) == 1


def test_a_mount_source_that_vanished_is_a_docker_error(tmp_path):
    """The scratch directory vanished before docker could start the container: infrastructure, exit 5 in repo run."""
    from dokimi import process
    from dokimi.repos.container import ContainerSpec, DockerError, DockerRunner, Mount

    spec = ContainerSpec(image="img:t", argv=["true"], mounts=[Mount(tmp_path / "gone", "/eval/run")])
    with pytest.raises(DockerError, match="mount source does not exist"):
        DockerRunner(runner=process.FakeRunner()).run(spec, stdout=tmp_path / "o", stderr=tmp_path / "e", timeout_s=5)


def test_strict_judge_dependent_is_checked_before_not_measured(capsys):
    """An arm that is both JUDGE-DEPENDENT and NOT MEASURED is invalid (4), not inconclusive (3): invalid is checked first."""
    from dokimi.commands import judge_reliability

    arms = {"a": {"label": "NOT MEASURED"}, "b": {"label": "JUDGE-DEPENDENT"}}
    with pytest.raises(SystemExit) as exc:
        judge_reliability._strict_cross_judge_exit(arms, "base")
    assert exc.value.code == 4
    assert "judge-dependent against `base`: b" in capsys.readouterr().err


def test_docker_29_permission_denied_on_inspect_is_a_docker_error():
    """Docker 29 CLI opens a socket it may not use with `permission denied while trying to connect to the docker API`:
    on `image inspect` that is no permission, not a missing image (exit 5, not 1)."""
    from dokimi import process
    from dokimi.repos.container import DockerError, DockerRunner

    denied = ("permission denied while trying to connect to the docker API at unix:///var/run/docker.sock: "
              "Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.47/images/img:t/json\": permission denied\n")
    fake = process.FakeRunner(lambda call: process.reply(stderr=denied, returncode=1))
    with pytest.raises(DockerError, match="permission denied while trying to connect to the docker API"):
        DockerRunner(runner=fake).image_exists("img:t")
