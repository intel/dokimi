# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo run` on a batch whose finished cell holds an unreadable `result.json` (contract §4). No docker, no LLM."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from repo_eval_fixture import FakeRunner, fake_harness, make_repo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {"root": root, "exe": exe, "profile": profile, "runner": runner, "out": tmp_path / "runs"}


def _run(s):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), "--agent-network", "host", "--arm", "guidance:raw",
            "--task", "fix_add", "--reps", "3"]
    args = build_parser().parse_args(argv)
    args.func(args)


@pytest.mark.parametrize(("content", "problem"), [
    (None, "not valid JSON"),  # None: the cell's own result.json, cut in half
    (b"[]", "not a JSON object"),
    (b"\xff{}", "not valid UTF-8 text"),
])
def test_an_unreadable_result_is_that_cells_error_and_the_other_cells_still_run(setup, capsys, content, problem):
    _run(setup)
    capsys.readouterr()
    batch = setup["out"] / "b1"
    bad = batch / "fix_add__guidance-raw__m-1__r1" / "result.json"
    content = bad.read_bytes()[:len(bad.read_bytes()) // 2] if content is None else content
    bad.write_bytes(content)
    (batch / "fix_add__guidance-raw__m-1__r3" / "result.json").unlink()
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit) as exit_:
        _run(setup)
    assert exit_.value.code == 1
    out = capsys.readouterr().out
    assert f"✗ fix_add__guidance-raw__m-1__r1: {bad}: {problem}" in out and "--force" in out
    assert "1 ran, 1 already done, 1 crashed" in out  # r2 reused, r3 run
    assert bad.read_bytes() == content  # left for the user to inspect
    assert len([s for s in setup["runner"].specs if "-p" in s.argv]) == 1
