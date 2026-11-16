# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit-code classification of a repo run: a harness that crashes after its first trace line keeps its cell."""

from __future__ import annotations

import pytest
import yaml

from dokimi.cli import build_parser
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
