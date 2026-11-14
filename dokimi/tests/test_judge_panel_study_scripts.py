# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""research/judge-panel/analyze.py stays pinned to the study's judges when the generation
experiment adds grades from others."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent / "research" / "judge-panel"
CC = "correct_complete"
MODELS = ("claude-opus-5", "claude-haiku-4-5", "gpt-5.6-luna", "grok-4.6", "nemotron-nano-12b")
ARMS = ("no_skill", "full", "placebo")


def _load(tmp_path, monkeypatch, **extra):
    monkeypatch.setattr(sys, "argv", ["analyze.py", str(tmp_path)])
    rows = [
        {"probe_id": "p1", "arm": arm, "model": model, "adversarial": False, "verdict": CC}
        for model in MODELS for arm in ARMS
    ]
    (tmp_path / "sample.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    grades = [{"key": f"p1|{arm}|{model}", "judge": judge, "verdict": CC}
              for model in MODELS for arm in ARMS for judge in ("kimi-k3", "gpt-6-sol")]
    (tmp_path / "grades.jsonl").write_text("\n".join(map(json.dumps, grades)), encoding="utf-8")
    src = (HERE / "analyze.py").read_text(encoding="utf-8").split("# ---------- 2. pairwise")[0]
    scope = {"__name__": "analyze", **extra}
    exec(compile(src, "analyze.py", "exec"), scope)  # nosec B102 - this directory's own script
    return scope


def test_analyze_ignores_grades_from_judges_outside_its_panel(tmp_path, monkeypatch):
    scope = _load(tmp_path, monkeypatch)
    assert "gpt-6-sol" not in scope["JUDGES"]
    assert scope["JUDGES"] == ["gpt-5.6-luna", "kimi-k3"]


def test_the_generation_study_can_still_load_every_judge(tmp_path, monkeypatch):
    scope = _load(tmp_path, monkeypatch, PANEL_ONLY=False)
    assert "gpt-6-sol" in {j for d in scope["V"].values() for j in d}
