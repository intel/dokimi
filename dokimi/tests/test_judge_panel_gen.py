# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""gen.py runs on the judges it needs, and names the one it lacks.

analyze.py used to run the whole study on import, so gen.py failed on any grade set that
lacked a judge of the study panel. The study now runs only from analyze.py's main block.
"""

import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")

RJ = Path(__file__).resolve().parents[1] / "research" / "judge-panel"
MODELS = ["claude-opus-5", "claude-haiku-4-5", "gpt-5.6-luna", "grok-4.6", "nemotron-nano-12b"]
V6 = ["correct_complete", "correct_missing_qualifier", "partially_correct",
      "wrong_reversed", "wrong_other", "abstain"]
# The judges gen.py's pairs and panels need. The original judge comes from the sample itself.
GEN_JUDGES = ["kimi-k2.5", "kimi-k3", "claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5-5",
              "deepseek-r1", "deepseek-v3.2", "gpt-6-luna", "gpt-6-sol"]


def _write(out, judges):
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    rows = [{"probe_id": f"p{p}", "model": m, "arm": arm, "question": "q", "answer": "a",
             "adversarial": False, "style": "plain", "verdict": "correct_complete"}
            for p in range(4) for m in MODELS for arm in ("no_skill", "full", "placebo")]
    (out / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    grades = [{"key": f"{r['probe_id']}|{r['arm']}|{r['model']}", "judge": j,
               "verdict": rng.choice(V6[:4])} for r in rows for j in judges]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))


def _gen(out):
    return subprocess.run([sys.executable, str(RJ / "gen.py"), str(out)],
                          capture_output=True, text=True, cwd=out.parent)


def test_gen_runs_without_the_study_panel(tmp_path):
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    assert (out / "gen_summary.json").exists()
    assert not (out / "tables_gen" / "per_judge_headline.csv").exists()  # study tables: analyze main only


def test_gen_names_a_missing_judge(tmp_path):
    out = tmp_path / "out"
    _write(out, [j for j in GEN_JUDGES if j != "kimi-k3"])

    r = _gen(out)

    assert r.returncode != 0
    assert "kimi-k3" in r.stderr
    assert "Traceback" not in r.stderr


def test_gen_runs_when_two_judges_agree_on_every_row(tmp_path):
    # Both judges give one label on every row, so the chance agreement is 1 and kappa is undefined.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    for g in grades:
        if g["judge"] in ("kimi-k2.5", "kimi-k3"):
            g["verdict"] = "correct_complete"
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    summary = json.loads((out / "gen_summary.json").read_text())
    pair = next(p for p in summary["pairs"] if p["old"] == "kimi-k2.5")
    assert pair["kappa_old_new"] is None  # 0/0: no kappa to report, not a perfect 1.0


def test_gen_names_a_judge_with_no_complete_triplet_for_a_model(tmp_path):
    # kimi-k2.5 has no grades for one answer model, so no complete no_skill/full/placebo triplet exists.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    grades = [g for g in grades if not (g["judge"] == "kimi-k2.5" and g["key"].endswith("|claude-opus-5"))]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    r = _gen(out)

    assert r.returncode != 0
    assert "kimi-k2.5" in r.stderr
    assert "claude-opus-5" in r.stderr
    assert "Traceback" not in r.stderr


def test_gen_skips_optional_judges_that_share_no_answer(tmp_path):
    # Two optional judges grade disjoint rows: their common answers are empty, so kappa is undefined.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    extra = [{"key": "p0|no_skill|claude-opus-5", "judge": "only-p0", "verdict": "correct_complete"},
             {"key": "p1|no_skill|claude-opus-5", "judge": "only-p1", "verdict": "correct_complete"}]
    with (out / "grades.jsonl").open("a") as f:
        f.write("".join(json.dumps(g) + "\n" for g in extra))

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    assert "Traceback" not in r.stderr
    assert (out / "gen_summary.json").exists()  # the outputs after the pair loop are written
    assert "only-p0" in (out / "tables_gen" / "judge_mean_kappa.csv").read_text()


def test_gen_lenient_delta_is_on_the_answers_both_judges_grade(tmp_path):
    # gpt-6-luna grades only probes p2 and p3. The delta must compare the two judges on those
    # answers, not gpt-5.6-luna's rate over all its rows (0.75 here) against the new judge's.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    sample = [json.loads(line) for line in (out / "sample.jsonl").read_text().splitlines()]
    for r in sample:
        if r["probe_id"] == "p0":
            r["verdict"] = "wrong_other"
    (out / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample))
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    grades = [g for g in grades if not (g["judge"] == "gpt-6-luna" and g["key"][:2] in ("p0", "p1"))]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))
    lenient = {"correct_complete", "correct_missing_qualifier", "partially_correct"}
    shared = {g["key"] for g in grades if g["judge"] == "gpt-6-luna"}
    new_votes = [g["verdict"] for g in grades if g["judge"] == "gpt-6-luna"]
    old_votes = [r["verdict"] for r in sample
                 if f"{r['probe_id']}|{r['arm']}|{r['model']}" in shared]
    expected = round(sum(v in lenient for v in new_votes) / len(new_votes)
                     - sum(v in lenient for v in old_votes) / len(old_votes), 4)

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    summary = json.loads((out / "gen_summary.json").read_text())
    pair = next(p for p in summary["pairs"] if p["old"] == "gpt-5.6-luna" and p["new"] == "gpt-6-luna")
    assert pair["lenient_rate_delta"] == expected


def test_analyze_names_a_study_judge_with_no_grades(tmp_path):
    # glm-5 is a study panel judge that these grades lack: name it before any table is written.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)

    r = subprocess.run([sys.executable, str(RJ / "analyze.py"), str(out)],
                       capture_output=True, text=True, cwd=out.parent)

    assert r.returncode != 0
    assert "glm-5" in r.stderr
    assert "Traceback" not in r.stderr
