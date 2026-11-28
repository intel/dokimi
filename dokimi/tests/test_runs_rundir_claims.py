# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run directories (docs/run-directories.md): what a failed claim leaves behind, and the paths a run hands out."""

from __future__ import annotations

import errno
import os
import sys

import pytest

from dokimi.artifacts import RUN_ID_ENV, load_artifact
from dokimi.runs import rundir
from dokimi.runs.rundir import open_run
from dokimi.utils import InputError


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv(RUN_ID_ENV, "r-1")
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "arms", "run"])
    return tmp_path / "results"


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
