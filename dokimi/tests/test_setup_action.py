# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/setup/action.yml: no pip cache, and an out-dir newline is refused before it is created.

A pip cache restored in a run that checks out the caller's dokimi-ref is the cache-poisoning path
CodeQL reports (actions/cache-poisoning/poisonable-step), so setup-python runs without a cache.
A newline in out-dir is refused before mkdir; the captured path is written without a newline.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

ACTION = Path(__file__).resolve().parents[2] / "actions/setup/action.yml"


def _steps() -> list[dict]:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    return action["runs"]["steps"]


def test_setup_python_runs_without_a_cache() -> None:
    setup_python = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert len(setup_python) == 1
    assert "cache" not in setup_python[0].get("with", {})
    assert "cache" not in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]


def _run_out_step(tmp_path: Path, out_dir: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # Runs the output step's script the way the runner does: bash -e -o pipefail, with the
    # runner's env files and RUNNER_TEMP pointed at tmp_path.
    script = next(s["run"] for s in _steps() if s.get("id") == "out")
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "GITHUB_ENV": str(tmp_path / "env"),
        "OUT": out_dir,
    }
    (tmp_path / "runner-temp").mkdir()
    for name in ("output", "env"):
        (tmp_path / name).touch()
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env=env, capture_output=True, text=True, check=False, cwd=cwd or tmp_path,
    )


def test_out_dir_default_is_written_as_a_clean_path(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "")
    assert result.returncode == 0, result.stderr
    output = (tmp_path / "output").read_text(encoding="utf-8")
    expected = (tmp_path / "runner-temp" / "dokimi-out").resolve()
    assert output == f"dir={expected}\n"


def test_a_cwd_ending_in_a_newline_is_refused(tmp_path: Path) -> None:
    # The path that reaches GITHUB_OUTPUT must not lose a newline at its end.
    cwd = tmp_path / "work\n"
    cwd.mkdir()
    result = _run_out_step(tmp_path, "out", cwd=cwd)
    assert result.returncode != 0
    assert "out-dir has a newline" in result.stdout
    assert (tmp_path / "output").read_text(encoding="utf-8") == ""


def test_out_dir_with_a_newline_is_refused_before_mkdir(tmp_path: Path) -> None:
    result = _run_out_step(tmp_path, "reports\n")
    assert result.returncode != 0
    assert "out-dir has a newline" in result.stdout
    assert not any(p.name.startswith("reports") for p in tmp_path.iterdir())
    assert (tmp_path / "output").read_text(encoding="utf-8") == ""


def test_the_action_declares_no_paid_or_max_usd_input() -> None:
    # A spending cap the action does not enforce must not ship as a safety input.
    inputs = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]
    assert "paid" not in inputs
    assert "max-usd" not in inputs


def test_the_action_exports_no_dokimi_runs_dir_or_max_usd() -> None:
    # DOKIMI_RUNS_DIR is read only by `dokimi runs`; the writers ignore it, so exporting it
    # misleads. DOKIMI_MAX_USD is read by no code. Neither is exported or documented here.
    text = ACTION.read_text(encoding="utf-8")
    assert "DOKIMI_RUNS_DIR" not in text
    assert "DOKIMI_MAX_USD" not in text
