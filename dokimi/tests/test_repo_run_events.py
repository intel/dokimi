# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run --candidates` and `--probes` reach a cell's ``events.jsonl`` (contract §4.1), with a fake runner and harness."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.container import WORKSPACE
from repo_eval_fixture import FakeRunner, fake_harness, make_repo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "out": tmp_path / "runs"}


def _run(s, *extra):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), "--agent-network", "host", *extra]
    args = build_parser().parse_args(argv)
    args.func(args)


def test_a_run_writes_the_probe_and_choice_rows_its_files_name(setup, monkeypatch, tmp_path):
    cands, probes = tmp_path / "cands.yaml", tmp_path / "probes.yaml"
    cands.write_text("sp: {import: [scipy]}\n")
    probes.write_text("cpu: [nproc]\n")
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([
        ["Bash", {"command": "nproc"}], ["Write", {"file_path": f"{WORKSPACE}/a.py", "content": "import scipy\n"}]]))
    _run(setup, "--arm", "guidance:none", "--task", "fix_add", "--candidates", str(cands), "--probes", str(probes))
    (events_file,) = (setup["out"] / "b1").glob("*/events.jsonl")
    kinds = [(e["kind"], e.get("pattern") or e.get("act")) for e in map(json.loads, events_file.read_text().splitlines())]
    assert ("probe", "nproc") in kinds and ("choice", "import") in kinds
