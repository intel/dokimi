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
