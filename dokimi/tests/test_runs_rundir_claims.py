# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run directories (docs/run-directories.md): what a failed claim leaves behind, and the paths a run hands out."""

from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import RUN_ID_ENV, load_artifact, save_artifact
from dokimi.runs import rm, rundir
from dokimi.runs.rundir import open_run
from dokimi.utils import InputError


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(RUN_ID_ENV, "r-1")
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "arms", "run"])
    return tmp_path / "results"


def _plan() -> dict:
    path = Path(__file__).resolve().parent / "fixtures" / "artifacts" / "plan.v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("given", [False, True], ids=["named", "empty-out-dir"])
def test_a_run_json_the_disk_cannot_take_leaves_no_directory_made_for_it(root, tmp_path, monkeypatch, given):
    """docs/run-directories.md: "When run.json itself cannot be written (a full disk) ... nothing is left
    behind, so the same name or output directory can be tried again"; an output directory that was there stays."""
    real = open

    def full(file, mode="r", *args, **kwargs):
        if mode == "x":
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return real(file, mode, *args, **kwargs)

    out = tmp_path / "out"
    if given:
        out.mkdir()
    args = {"out_dir": out} if given else {"name": "n"}
    monkeypatch.setattr(rundir, "open", full, raising=False)
    with pytest.raises(InputError, match="No space left on device"):
        open_run(root, **args)
    if given:
        assert os.listdir(out) == []
    else:
        assert not (root / "n").exists()

    monkeypatch.delattr(rundir, "open")
    assert load_artifact("run", open_run(root, **args).path / "run.json")["status"] == "running"


def test_a_name_whose_run_runs_rm_removed_can_be_used_again(root, monkeypatch):
    """docs/run-directories.md: a directory "runs rm or runs gc emptied, so its name can be used again";
    runs rm removes a run's files and keeps its directory."""
    with open_run(root, name="nightly-7"):
        pass
    rm.apply(rm.plan_rm(root, "r-1"))
    assert os.listdir(root / "nightly-7") == []
    monkeypatch.setenv(RUN_ID_ENV, "r-2")

    record = load_artifact("run", open_run(root, name="nightly-7").path / "run.json")

    assert (record["run_id"], record["name"], record["status"]) == ("r-2", "nightly-7", "running")


@pytest.mark.parametrize("kind", ["plan", "result", "run"])
def test_a_plan_saved_past_write_plan_cannot_make_an_unmeasured_run_complete(root, kind):
    """docs/run-directories.md: "a run is refused complete while a planned cell has no measured status";
    artifact_path refuses the kinds the run writes itself, so no plan reaches the disk unknown to the run."""
    with open_run(root, name="n") as run:
        with pytest.raises(ValueError, match="write_plan"):
            save_artifact(kind, _plan(), run.artifact_path(kind))

    record = load_artifact("run", run.path / "run.json")
    assert (record["status"], record["artifacts"]) == ("complete", [])
    assert sorted(os.listdir(run.path)) == ["run.json"]
