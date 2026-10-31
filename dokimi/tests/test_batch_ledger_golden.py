# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Golden pins of what a ``repo run`` batch writes: execution ids, cell names, the records and the files
around them. A refactor of the batch ledger must leave every value here unchanged; a change that moves one
is a change of identity (an existing batch would be refused or re-run), so it needs its own decision."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.batch_ledger import execution_id
from dokimi.repos.harness_profile import ProfileError, load_profile
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo

FIXED_SHA = "0" * 63 + "1"
FIXED_VERSION = "2.1.0"

# (tools, delegation) -> execution id of the built-in claude-code profile, with a fixed harness digest and
# version. ``None``: the profile refuses that delegation for that tool mode.
GOLDEN_CLAUDE_CODE_IDS = {
    ("all", "native"): "e6eabec71f42",
    ("all", "none"): "2324b5c59f99",
    ("none", "native"): "7d8aa618677b",
    ("none", "none"): "7d8aa618677b",
    ("read", "native"): "e9dbd28050f2",
    ("read", "none"): "e9dbd28050f2",
}

# The sample batch (the fake profile and binary of the fixture): every cell's execution record, by cell name.
GOLDEN_SAMPLE_CELLS = {
    "fix_add__guidance-none__m-1__r1": {
        "id": "d8d2e00d02f7",
        "tools": "all",
        "delegation": "native",
    },
    "fix_add__guidance-none__m-1__r2": {
        "id": "d8d2e00d02f7",
        "tools": "all",
        "delegation": "native",
    },
    "fix_add__guidance-raw__m-1__r1": {
        "id": "d8d2e00d02f7",
        "tools": "all",
        "delegation": "native",
    },
    "fix_add__guidance-raw__m-1__r2": {
        "id": "d8d2e00d02f7",
        "tools": "all",
        "delegation": "native",
    },
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {
        "root": root,
        "exe": exe,
        "profile": profile,
        "runner": runner,
        "out": tmp_path / "runs",
    }


def _run(s, *extra):
    argv = [
        "repo",
        "run",
        f"{s['root']}@HEAD",
        "--manifest",
        ".agent-eval/repo-eval.yaml",
        "--harness",
        str(s["profile"]),
        "--bin",
        str(s["exe"]),
        "--model",
        "m-1",
        "--batch",
        "b1",
        "--out",
        str(s["out"]),
        "--cache-root",
        str(s["out"] / "caches"),
        "--agent-network",
        "host",
        *extra,
    ]
    args = build_parser().parse_args(argv)
    args.func(args)


def _cell_snapshot(rd):
    result = json.loads((rd / "result.json").read_text(encoding="utf-8"))
    meta = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    return {
        "files": sorted(p.name for p in rd.iterdir()),
        "meta_keys": sorted(meta),
        "result_keys": sorted(result),
        "meta_execution": meta["execution"],
        "result_execution": result["execution"],
        "result": {
            k: result[k]
            for k in (
                "task",
                "family",
                "arm",
                "model",
                "model_resolved",
                "rep",
                "harness",
                "harness_version",
                "score_task",
                "score_max",
            )
        },
        "grade": {k: result["grade"][k] for k in ("status", "success")},
        "agent_cost_usd": result["agent"]["cost_usd"],
    }


def test_execution_ids_of_the_builtin_profile_are_pinned():
    profile = load_profile("claude-code")
    ids = {}
    for tools in sorted(profile.tools):
        for delegation in ("native", "none"):
            try:
                ids[(tools, delegation)] = execution_id(
                    profile, tools, delegation, FIXED_SHA, FIXED_VERSION
                )
            except ProfileError:
                continue
    assert ids == GOLDEN_CLAUDE_CODE_IDS


def test_a_sample_batch_writes_the_pinned_cells_records_and_files(setup, capsys):
    _run(setup, "--arm", "guidance:none,guidance:raw", "--task", "fix_add", "--reps", "2")
    assert "4 ran, 0 already done" in capsys.readouterr().out
    batch = setup["out"] / "b1"
    assert sorted(p.name for p in batch.iterdir()) == ["_eval", *sorted(GOLDEN_SAMPLE_CELLS)]
    for name, expected in GOLDEN_SAMPLE_CELLS.items():
        rd = batch / name
        meta = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
        assert meta["execution"] == expected
        assert json.loads((rd / "result.json").read_text(encoding="utf-8"))["execution"] == expected
    snapshot = _cell_snapshot(batch / "fix_add__guidance-raw__m-1__r1")
    assert snapshot["files"] == sorted(snapshot["files"]) and "result.json" in snapshot["files"]
    assert (
        snapshot["result"]["harness"] == "fake" and snapshot["result"]["harness_version"] == "1.2.3"
    )
    assert snapshot["grade"] == {"status": "graded", "success": True}
    assert snapshot["result_keys"] == sorted(snapshot["result_keys"])
    plan = json.loads((batch / "_eval" / "plan.json").read_text(encoding="utf-8"))
    assert plan == {"cells": sorted(GOLDEN_SAMPLE_CELLS)}


def test_a_resumed_batch_skips_the_finished_cells_and_refuses_another_execution(setup, capsys):
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    capsys.readouterr()
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add")
    assert "0 ran, 1 already done" in capsys.readouterr().out
    setup["runner"].specs.clear()
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", "--tools", "read")
    err = capsys.readouterr().err
    assert "holds cells of another execution" in err
    assert not [s for s in setup["runner"].specs if "-p" in s.argv]


GOLDEN_SKILL_PIN = {
    "guidance:raw+skill:lint": [
        ["lint", "546383eb95a51d2f5175abd9cd5837664f23eea75c74b4fd2c85054df0aab85d"]
    ],
}


def test_a_skill_arm_pins_its_skill_tree_in_the_batch(setup, capsys):
    skill = setup["root"] / ".agent-eval/skills/lint"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: lint\ndescription: lint\n---\nLint the code.\n", encoding="utf-8"
    )
    git(setup["root"], "add", "-A")
    git(setup["root"], "commit", "-qm", "skill")  # the eval content is read at HEAD
    _run(setup, "--arm", "guidance:raw+skill:skills/lint", "--task", "fix_add")
    assert "1 ran" in capsys.readouterr().out
    pin = json.loads((setup["out"] / "b1" / "_eval" / "skills.json").read_text(encoding="utf-8"))
    assert pin == GOLDEN_SKILL_PIN
    assert [p.parent.name for p in (setup["out"] / "b1").glob("*/result.json")] == [
        "fix_add__guidance-raw_skill-lint__m-1__r1"
    ]
