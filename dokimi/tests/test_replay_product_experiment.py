# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/replay_product_experiment.py — the offline product experiment pilot."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "replay_product_experiment.py"
_spec = importlib.util.spec_from_file_location("replay_product_experiment", _SCRIPT)
assert _spec and _spec.loader
rpe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rpe)


def test_fresh_control_violating_constraints_does_not_pass(tmp_path, monkeypatch):
    cell = tmp_path / "cell"
    cell.mkdir()
    out = tmp_path / "run1"
    out.mkdir()
    control = {"control": "positive_patched", "seed": 1000, "family": "f"}
    rpe.write_json(cell / "case.json", {"source": "test_fixture", "controls": [control],
                                        "evaluator": {"root": "r", "python": "p"}})
    monkeypatch.setenv("DOKIMI_OUTPUT_DIR", str(out))
    monkeypatch.setattr(rpe, "evaluate_control", lambda *_: {
        "correct": True, "predict_backend": "accelerated", "constraint_ok": False})
    for arm in ("correctness", "correctness-and-backend"):
        rpe.run_cell(argparse.Namespace(cell=cell, arm=arm))
        assert (out / "reward.txt").read_text(encoding="utf-8") == "0\n"
        assert json.loads((out / "evidence.json").read_text())["verdicts"]["constraint_ok"] is False


def test_suite_without_product_is_rejected_with_a_clear_error(tmp_path):
    suite = rpe.build_suite(rpe.TASK, rpe.CRITERIA)
    prompts = {case["id"]: case["prompt"] for case in suite["cases"]}
    args = argparse.Namespace(skill_fixture=False, controls=None, evaluator_root=None)
    with pytest.raises(ValueError, match="product-named"):
        rpe.materialize_tasks(args, tmp_path, prompts)


def test_harness_error_is_an_infrastructure_error(tmp_path, monkeypatch):
    out = tmp_path / "replay"

    def fake_run(cmd, **_):
        rpe.write_json(Path(cmd[cmd.index("--out") + 1]), {"results": [{
            "task": "unicode-key", "harness": "with-skill", "repeat": 1, "passed": False,
            "elapsed_sec": 0.0, "output_dir": str(tmp_path / "missing"),
            "metrics": {"error": "harness failed"}, "reward": None}]})
        return argparse.Namespace(returncode=1)

    monkeypatch.setattr(rpe.subprocess, "run", fake_run)
    rpe.run(argparse.Namespace(out=out, suite=None, skill_fixture=True, skill_file=None))
    cell = json.loads((out / "response.json").read_text())["cells"][0]
    assert cell["status"] == "infrastructure_error"
    assert cell["error"] == "harness failed"


def test_skill_fixture_scores_all_cells_under_quoted_path(tmp_path):
    out = tmp_path / "user's run"
    rpe.run(argparse.Namespace(out=out, suite=None, skill_fixture=True, skill_file=None))
    cells = json.loads((out / "response.json").read_text())["cells"]
    assert len(cells) == 6
    assert {(cell["arm"], cell["status"]) for cell in cells} == {
        ("without-skill", "task_failed"), ("with-skill", "passed")}
