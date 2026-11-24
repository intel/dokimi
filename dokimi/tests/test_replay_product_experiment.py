# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/replay_product_experiment.py — the offline product experiment pilot."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

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
