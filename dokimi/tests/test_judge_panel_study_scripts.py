# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""research/judge-panel/analyze.py stays pinned to the study's judges when the generation
experiment adds grades from others."""

import hashlib
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


def test_a_grade_on_a_replaced_answer_is_not_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze.py", str(tmp_path)])
    fp = lambda text: hashlib.sha256(text.encode()).hexdigest()[:16]  # noqa: E731 - as judge_panel.py
    key = "p1|full|claude-opus-5"
    rows = [{"probe_id": "p1", "arm": arm, "model": model, "adversarial": False, "verdict": CC,
             "answer": "the replacement answer"} for model in MODELS for arm in ARMS]
    (tmp_path / "sample.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    row = rows[ARMS.index("full") + len(ARMS) * MODELS.index("claude-opus-5")]
    grades = [{"key": key, "judge": "kimi-k3", "verdict": "wrong_other", "answer_sha": fp("the old answer")},
              {"key": key, "judge": "gpt-5.6-luna", "verdict": CC, "answer_sha": fp(row["answer"])}]
    (tmp_path / "grades.jsonl").write_text("\n".join(map(json.dumps, grades)), encoding="utf-8")
    src = (HERE / "analyze.py").read_text(encoding="utf-8").split("# ---------- 2. pairwise")[0]
    scope = {"__name__": "analyze"}
    exec(compile(src, "analyze.py", "exec"), scope)  # nosec B102 - this directory's own script
    assert "kimi-k3" not in scope["V"][key]  # its regrade of the old answer is stale
    assert scope["V"][key]["gpt-5.6-luna"] == CC


def _write_disjoint_judges(tmp_path):
    """Two judges with no row in common, and the original judge on every row."""
    rows, grades = [], []
    for probe, judge in (("p1", "claude-sonnet-5"), ("p2", "gpt-6-luna")):  # no row has both
        for model in MODELS:
            for arm in ARMS:
                key = f"{probe}|{arm}|{model}"
                rows.append({"probe_id": probe, "arm": arm, "model": model, "adversarial": False,
                             "verdict": CC, "answer": key})
                grades.append({"key": key, "judge": judge, "verdict": CC})
    (tmp_path / "sample.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    (tmp_path / "grades.jsonl").write_text("\n".join(map(json.dumps, grades)), encoding="utf-8")


def test_judges_with_disjoint_rows_give_an_empty_kappa_cell_not_a_crash(tmp_path):
    import subprocess

    _write_disjoint_judges(tmp_path)
    (tmp_path / "tables").mkdir()
    subprocess.run([sys.executable, str(HERE / "analyze.py"), str(tmp_path)], check=True,
                   capture_output=True, timeout=300)  # nosec B603 - this directory's own script
    assert (tmp_path / "tables" / "pairwise_kappa.csv").exists()
    # both judges grade every row the same way: chance agreement is 1, kappa is undefined
    rows = [json.loads(line) for line in (tmp_path / "sample.jsonl").read_text(encoding="utf-8").splitlines()]
    grades = [{"key": r["answer"], "judge": j, "verdict": CC} for r in rows
              for j in ("claude-sonnet-5", "gpt-6-luna")]
    (tmp_path / "grades.jsonl").write_text("\n".join(map(json.dumps, grades)), encoding="utf-8")
    subprocess.run([sys.executable, str(HERE / "analyze.py"), str(tmp_path)], check=True,
                   capture_output=True, timeout=300)  # nosec B603 - this directory's own script
    cells = (tmp_path / "tables" / "pairwise_kappa.csv").read_text(encoding="utf-8").splitlines()
    assert any(c.startswith("claude-sonnet-5,gpt-6-luna,") and c.split(",")[3:6] == ["", "", ""]
               for c in cells)


def test_the_generation_study_runs_when_a_judge_has_no_shared_rows(tmp_path):
    import subprocess

    _write_disjoint_judges(tmp_path)
    (tmp_path / "tables_gen").mkdir()
    subprocess.run([sys.executable, str(HERE / "gen.py"), str(tmp_path)], check=True,
                   capture_output=True, timeout=300)  # nosec B603 - this directory's own script
    assert (tmp_path / "gen_summary.json").exists()

