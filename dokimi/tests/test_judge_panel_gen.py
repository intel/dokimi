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


def test_gen_lenient_delta_ignores_rows_only_the_new_judge_grades(tmp_path):
    # As in the study, gpt-6-luna also grades no_skill repeats that gpt-5.6-luna never saw.
    # The judges agree on every shared answer, so those extra rows must not move the delta.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    sample = [json.loads(line) for line in (out / "sample.jsonl").read_text().splitlines()]
    for r in sample:
        if r["probe_id"] == "p0":
            r["verdict"] = "wrong_other"
    (out / "sample.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sample))
    verdict = {f"{r['probe_id']}|{r['arm']}|{r['model']}": r["verdict"] for r in sample}
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    for g in grades:
        if g["judge"] == "gpt-6-luna":
            g["verdict"] = verdict[g["key"]]
    repeats = [dict(r, arm="no_skill_repeat", verdict=None) for r in sample if r["arm"] == "no_skill"]
    (out / "repeats.jsonl").write_text("".join(json.dumps(r) + "\n" for r in repeats))
    grades += [{"key": f"{r['probe_id']}|{r['arm']}|{r['model']}", "judge": "gpt-6-luna",
                "verdict": "correct_complete"} for r in repeats]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    summary = json.loads((out / "gen_summary.json").read_text())
    pair = next(p for p in summary["pairs"] if p["old"] == "gpt-5.6-luna" and p["new"] == "gpt-6-luna")
    assert pair["lenient_rate_delta"] == 0.0


def test_analyze_names_a_study_judge_with_no_grades(tmp_path):
    # glm-5 is a study panel judge that these grades lack: name it before any table is written.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)

    r = subprocess.run([sys.executable, str(RJ / "analyze.py"), str(out)],
                       capture_output=True, text=True, cwd=out.parent)

    assert r.returncode != 0
    assert "glm-5" in r.stderr
    assert "Traceback" not in r.stderr


def test_gen_lenient_delta_keeps_four_decimals(tmp_path):
    # One of 60 shared answers differs, so the delta is -1/60: -0.0167 at four decimals, -0.0 at one.
    out = tmp_path / "out"
    _write(out, GEN_JUDGES)
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    for g in grades:
        if g["judge"] == "gpt-6-luna":
            g["verdict"] = "wrong_other" if g["key"] == "p0|full|claude-opus-5" else "correct_complete"
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))

    r = _gen(out)

    assert r.returncode == 0, r.stderr[-800:]
    summary = json.loads((out / "gen_summary.json").read_text())
    pair = next(p for p in summary["pairs"] if p["old"] == "gpt-5.6-luna" and p["new"] == "gpt-6-luna")
    assert pair["lenient_rate_delta"] == -0.0167


PANEL = ["claude-sonnet-5", "gpt-6-luna", "grok-4.6", "kimi-k3", "deepseek-v3.2", "glm-5", "qwen3-vl-235b"]


def _analyze(out):
    return subprocess.run([sys.executable, str(RJ / "analyze.py"), str(out)],
                          capture_output=True, text=True, cwd=out.parent)


def _csv(out, name):
    import csv

    with (out / "tables" / name).open() as fh:
        return list(csv.DictReader(fh))


def _rewrite_grades(out, edit):
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text().splitlines()]
    grades = [g for g in (edit(g) for g in grades) if g is not None]
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades))


def test_analyze_leaves_a_constant_label_pair_without_kappa(tmp_path):
    # glm-5 and qwen3-vl-235b give one label on every row: kappa is 0/0, not 1.0, and the mean skips it.
    out = tmp_path / "out"
    _write(out, PANEL)

    def edit(g):
        if g["judge"] in ("glm-5", "qwen3-vl-235b"):
            g["verdict"] = "correct_complete"
        return g

    _rewrite_grades(out, edit)

    r = _analyze(out)

    assert r.returncode == 0, r.stderr[-800:]
    rows = _csv(out, "pairwise_kappa.csv")
    pair = next(x for x in rows if (x["judge_a"], x["judge_b"]) == ("glm-5", "qwen3-vl-235b"))
    assert pair["kappa_6way"] == pair["kappa_lenient"] == pair["kappa_strict"] == ""
    known = [float(x["kappa_lenient"]) for x in rows
             if "glm-5" in (x["judge_a"], x["judge_b"]) and x["kappa_lenient"] != ""]
    assert known
    summary = json.loads((out / "summary.json").read_text())
    assert summary["mean_kappa_lenient"]["glm-5"] == round(sum(known) / len(known), 3)


def test_analyze_mean_score_rule_counts_seven_partially_correct(tmp_path):
    # Seven scores of 0.4 average to 0.39999999999999997: the mean rule must still reach 0.4.
    out = tmp_path / "out"
    _write(out, PANEL)

    def edit(g):
        g["verdict"] = "partially_correct" if "|full|" in g["key"] else "wrong_other"
        return g

    _rewrite_grades(out, edit)

    r = _analyze(out)

    assert r.returncode == 0, r.stderr[-800:]
    table = {(x["rule"], x["answer_model"]): x for x in _csv(out, "aggregation_rules.csv")}
    assert table[("median_score>=0.4", "claude-opus-5")]["added"] == "1.0"
    assert table[("mean_score>=0.4", "claude-opus-5")]["added"] == "1.0"


def test_analyze_names_a_judge_with_no_grades_for_one_answer_model(tmp_path):
    out = tmp_path / "out"
    _write(out, PANEL)
    _rewrite_grades(out, lambda g: None if g["judge"] == "glm-5" and g["key"].endswith("|claude-opus-5") else g)

    r = _analyze(out)

    assert r.returncode != 0
    assert "glm-5" in r.stderr
    assert "claude-opus-5" in r.stderr
    assert "Traceback" not in r.stderr


def test_analyze_names_the_original_judge_with_no_verdict_for_one_answer_model(tmp_path):
    out = tmp_path / "out"
    _write(out, PANEL)
    sample = [json.loads(line) for line in (out / "sample.jsonl").read_text().splitlines()]
    for s in sample:
        if s["model"] == "claude-opus-5" and s["arm"] == "no_skill":
            s["verdict"] = None
    (out / "sample.jsonl").write_text("".join(json.dumps(s) + "\n" for s in sample))

    r = _analyze(out)

    assert r.returncode != 0
    assert "gpt-5.6-luna" in r.stderr
    assert "claude-opus-5" in r.stderr
    assert "Traceback" not in r.stderr


def test_analyze_leaves_agreement_empty_for_judges_with_no_answer_in_common(tmp_path):
    out = tmp_path / "out"
    _write(out, PANEL)
    only = {"glm-5": "p0", "qwen3-vl-235b": "p1"}
    _rewrite_grades(out, lambda g: None if g["judge"] in only and not g["key"].startswith(only[g["judge"]])
                    else g)

    r = _analyze(out)

    assert r.returncode == 0, r.stderr[-800:]
    pair = next(x for x in _csv(out, "pairwise_kappa.csv")
                if (x["judge_a"], x["judge_b"]) == ("glm-5", "qwen3-vl-235b"))
    assert pair["n"] == "0"
    assert pair["agree_lenient"] == pair["kappa_lenient"] == ""
