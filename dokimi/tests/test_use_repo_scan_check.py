# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""use-repo-scan's check reads `repo scan --json` from either shape.

The task is pinned to its base commit, and the check runs that commit's cli.py, which prints
the scan payload flat. A cli.py from after the cli_output.v1 envelope prints it under `data`.
The check must grade both, so it cannot assume one shape.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parents[2] / ".agent-eval/tasks/use-repo-scan/check.py"

SCAN = {
    "files": ["AGENTS.md", "README.md"],
    "claims": [
        {"file": "AGENTS.md", "claim": "src/tinycalc/parser.py", "status": "fail"},
        {"file": "AGENTS.md", "claim": "docs/releasing.md", "status": "fail"},
        {"file": "AGENTS.md", "claim": "tests/", "status": "ok"},
    ],
}


def _load_check():
    spec = importlib.util.spec_from_file_location("use_repo_scan_check", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "stdout",
    [
        pytest.param(json.dumps(SCAN), id="flat-task-base"),
        pytest.param(
            json.dumps({"schema_version": 1, "kind": "repo_scan", "data": SCAN}), id="cli-output-v1"
        ),
    ],
)
def test_scan_metrics_read_either_shape(monkeypatch, tmp_path, stdout):
    if not CHECK.is_file():
        pytest.skip("the task tree (.agent-eval) is not in this checkout")
    check = _load_check()
    toy = tmp_path / "toy"
    toy.mkdir()
    (toy / "repo-eval.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    monkeypatch.setattr(check, "TOY", toy)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(check.subprocess, "run", fake_run)
    out = check.manifest_metrics()
    assert out["scan_covers_agents_md"] is True
    assert out["scan_reports_both_fails"] is True
    assert out["scan_n_fail"] == 2
