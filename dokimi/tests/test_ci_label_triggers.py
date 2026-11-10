# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""ci.yml's label-gated checks must run again when a maintainer changes a label.

The `Fragment for package changes` and `schema_compat` steps read
`skip-changelog` and `schema-break-ok` from github.event.pull_request.labels. That
list comes from the event that started the run, and a re-run reuses the same event,
so a label added after the push starts a run only if the pull_request trigger lists
the labeled and unlabeled types.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def _ci() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _trigger(wf: dict) -> dict:
    return wf.get("on") or wf[True]  # PyYAML reads the bare key `on` as True


def test_pull_request_trigger_fires_on_label_changes():
    pr = _trigger(_ci())["pull_request"] or {}
    types = set(pr.get("types") or [])
    assert {"opened", "synchronize", "reopened", "labeled", "unlabeled"} <= types


def _schema_compat_step() -> str:
    steps = _ci()["jobs"]["static"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "schema_compat vs the PR base")


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="runs the step's bash")
@pytest.mark.parametrize(("status", "label", "step_exit"), [
    (0, "false", 0), (0, "true", 0),
    (1, "false", 1), (1, "true", 0),
    (2, "false", 2), (2, "true", 2),
])
def test_the_label_overrides_a_breaking_edit_but_not_a_base_git_cannot_read(
        tmp_path, status, label, step_exit):
    """schema_compat exits 1 for a breaking edit and 2 for an unreadable base: only the
    first may be accepted with ``schema-break-ok``."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "python").write_text(f"#!/bin/sh\nexit {status}\n", encoding="utf-8")
    (stub / "python").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "BASE_REF": "main",
           "OVERRIDE": label, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")}
    done = subprocess.run(["bash", "-c", _schema_compat_step()], cwd=tmp_path, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == step_exit
