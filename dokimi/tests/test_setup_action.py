# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/setup/action.yml: no pip cache, and an out-dir newline is refused before it is created.

A pip cache restored in a run that checks out the caller's dokimi-ref is the cache-poisoning path
CodeQL reports (actions/cache-poisoning/poisonable-step), so setup-python runs without a cache.
A newline in out-dir is refused before mkdir, and the captured path keeps a trailing newline so the
check after the capture sees it.
"""

from __future__ import annotations

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


def test_out_dir_newline_is_refused_before_mkdir() -> None:
    run = next(s["run"] for s in _steps() if s.get("id") == "out")
    refuse = run.index("*$'\\n'*")
    assert refuse < run.index('mkdir -p "$out"')


def test_out_dir_capture_keeps_a_trailing_newline() -> None:
    run = next(s["run"] for s in _steps() if s.get("id") == "out")
    assert "printf x" in run and "${dir%x}" in run


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
