# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit-code classification of a repo run: a harness that crashes after its first trace line keeps its cell."""

from __future__ import annotations

import json

import pytest
import yaml

from dokimi import process
from dokimi.cli import build_parser
from dokimi.repos.container import DockerRunner as RealDockerRunner
from repo_eval_fixture import FakeRunner, fake_harness, make_repo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("SECRET_NOT_ALLOWED", "x")
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "out": tmp_path / "runs"}


def _run(s, *extra, network=True, source=None):
    argv = ["repo", "run", source or f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), *(["--agent-network", "host"] if network else []), *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def _stub_harness(tmp_path, body):
    """A harness that answers --version, then runs ``body`` (the fixture binary is pinned by a golden digest)."""
    exe = tmp_path / "stub-agent"
    exe.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                   'if sys.argv[1:] == ["--version"]:\n    print("1.2.3 (Stub Agent)")\n    sys.exit(0)\n' + body,
                   encoding="utf-8")
    exe.chmod(0o755)
    return exe


def test_a_harness_that_crashes_after_its_start_record_has_a_trace_and_is_not_a_crash(setup, tmp_path):
    # The start-up record is a trace line: the agent started, so the cell keeps its result (no exit 5).
    body = 'print(json.dumps({"type": "system", "subtype": "init", "model": "m-1"}))\nsys.exit(1)\n'
    _run({**setup, "exe": _stub_harness(tmp_path, body)}, "--arm", "guidance:raw", "--task", "fix_add")
    assert (setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").is_file()


def test_a_text_harness_whose_stdout_is_its_trace_is_not_a_crash(setup, tmp_path):
    # trace: text has no events, so its stdout is the only trace line: a crash after an answer keeps the cell.
    prof = yaml.safe_load(setup["profile"].read_text(encoding="utf-8"))
    prof["trace"] = "text"
    text = tmp_path / "text-profile.yaml"
    text.write_text(yaml.safe_dump(prof), encoding="utf-8")
    body = 'print("The answer is 4.")\nsys.exit(1)\n'
    _run({**setup, "profile": text, "exe": _stub_harness(tmp_path, body)}, "--arm", "guidance:raw",
         "--task", "fix_add")
    assert (setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").is_file()


DAEMON_DOWN = ("failed to connect to the docker API at unix:///var/run/docker.sock. Check if the path is correct and "
               "if the daemon is running: dial unix /var/run/docker.sock: connect: no such file or directory\n")


@pytest.mark.parametrize("extra, stderr", [((), DAEMON_DOWN), (("--rebuild-image",), "ERROR: " + DAEMON_DOWN)],
                         ids=["inspect", "build"])
def test_repo_run_with_the_docker_daemon_down_exits_5_before_any_cell(setup, monkeypatch, capsys, extra, stderr):
    """The quickstart's exit-5 example: docker 29 prints `ERROR: failed to connect ...` on a build, bare on inspect."""
    docker = RealDockerRunner(runner=process.FakeRunner(lambda call: process.reply(stderr=stderr, returncode=1)))
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: docker)
    with pytest.raises(SystemExit) as exc:
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", *extra)
    assert exc.value.code == 5
    assert "failed to connect to the docker API" in capsys.readouterr().err
    assert not list((setup["out"] / "b1").glob("fix_add__*"))


def test_a_harness_whose_trace_is_unreadable_is_graded_not_crashed(setup, tmp_path):
    # A recorded trace error means the agent ran: the cell keeps result.json (behavioural-security.md), no exit 5.
    body = 'print("this is not stream-json")\nsys.exit(1)\n'
    _run({**setup, "exe": _stub_harness(tmp_path, body)}, "--arm", "guidance:raw", "--task", "fix_add")
    res = json.loads((setup["out"] / "b1/fix_add__guidance-raw__m-1__r1/result.json").read_text(encoding="utf-8"))
    assert res["agent"]["trace_errors"]
