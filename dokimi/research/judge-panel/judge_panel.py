#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Judge-panel study: re-grade a stratified sample of Dokimi probe answers with several judges.

Steps (each resumable; LLM calls cached in OUT/cache, never in the study cache):
  sample  -> OUT/sample.jsonl   (all arms for sampled (probe, model) cells + orig luna verdict)
  repeat  -> OUT/repeats.jsonl  (fresh no_skill answer, cache bypassed: the noise-floor arm)
  grade   -> OUT/grades.jsonl   (one row per (row_key, judge))
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

STUDY = Path(os.environ["JP_STUDY"])  # the skill-novelty study checkout
sys.path.insert(0, str(STUDY / "tools"))
import llm  # noqa: E402
import run_probes as rp  # noqa: E402

OUT = Path(os.environ.get("JP_OUT", "judge-panel-out"))
OUT.mkdir(parents=True, exist_ok=True)
llm.CACHE = OUT / "cache"
llm.CACHE.mkdir(exist_ok=True)
llm.LOG = OUT / "llm_calls.jsonl"
llm.NO_TEMPERATURE.update(
    {"gpt-6-luna", "kimi-k3"}
)  # both reject temperature=0 (gpt-6 runs at t=1)

ANSWER_MODELS = [
    "claude-opus-5",
    "gpt-5.6-luna",
    "grok-4.6",
    "claude-haiku-4-5",
    "nemotron-nano-12b",
]
ARMS = ["no_skill", "root", "routed", "full", "placebo", "shuffled"]
JUDGES = [
    "claude-sonnet-5",
    "gpt-6-luna",
    "grok-4.6",
    "kimi-k3",
    "deepseek-v3.2",
    "glm-5",
    "qwen3-vl-235b",
]


def key(r):
    return f"{r['probe_id']}|{r['arm']}|{r['model']}"


def do_sample(per_model: int):
    rows = [json.loads(l) for l in (STUDY / "data/probe_results.jsonl").open()]
    cell = defaultdict(dict)
    for r in rows:
        if r.get("verdict") and r["verdict"] != "ungraded":
            cell[(r["probe_id"], r["model"])][r["arm"]] = r
    rnd = random.Random(20261108)
    out = []
    for m in ANSWER_MODELS:
        full = [(p, c) for (p, mm), c in cell.items() if mm == m and all(a in c for a in ARMS)]
        strata = defaultdict(list)
        for _p, c in full:
            strata[(c["no_skill"]["skill"], c["no_skill"]["adversarial"])].append(c)
        tot = len(full)
        picked = []
        for _s, cs in sorted(strata.items()):
            k = max(1, round(per_model * len(cs) / tot))
            picked += rnd.sample(cs, min(k, len(cs)))
        for c in picked:
            for a in ARMS:
                r = c[a]
                out.append(
                    {
                        k: r.get(k)
                        for k in (
                            "probe_id",
                            "atom_id",
                            "skill",
                            "arm",
                            "model",
                            "model_family",
                            "adversarial",
                            "style",
                            "question",
                            "answer",
                            "verdict",
                            "verdict2",
                            "grader2",
                        )
                    }
                )
        print(m, "eligible", tot, "picked", len(picked))
    with (OUT / "sample.jsonl").open("w") as fh:
        for r in out:
            fh.write(json.dumps(r) + "\n")
    print("rows", len(out))


def do_repeat(workers: int):
    rows = [json.loads(l) for l in (OUT / "sample.jsonl").open()]
    base = [r for r in rows if r["arm"] == "no_skill"]

    def run(r):
        rec = llm.chat(
            r["model"],
            [{"role": "system", "content": rp.SYSTEM}, {"role": "user", "content": r["question"]}],
            temperature=0.0,
            max_tokens=1400,
            use_cache=False,
            tag=f"repeat|{r['model']}|{r['probe_id']}",
        )
        return {
            **r,
            "arm": "no_skill_repeat",
            "answer": rec["text"],
            "verdict": None,
            "verdict2": None,
            "grader2": None,
        }

    res = llm.pmap(run, base, workers=workers, desc="repeats")
    ok = [x for x in res if isinstance(x, dict)]
    print("repeat ok", len(ok), "failed", len(res) - len(ok))
    with (OUT / "repeats.jsonl").open("w") as fh:
        for r in ok:
            fh.write(json.dumps(r) + "\n")


def do_grade(workers: int, judges: list[str]):
    probes = {}
    for l in (STUDY / "data/probes.jsonl").open():
        p = json.loads(l)
        probes[p["probe_id"]] = p
    rows = [json.loads(l) for l in (OUT / "sample.jsonl").open()]
    if (OUT / "repeats.jsonl").exists():
        rows += [json.loads(l) for l in (OUT / "repeats.jsonl").open()]
    done = set()
    gp = OUT / "grades.jsonl"
    if gp.exists():
        for l in gp.open():
            g = json.loads(l)
            if g.get("verdict"):
                done.add((g["key"], g["judge"]))
    jobs = [(r, j) for j in judges for r in rows if (key(r), j) not in done]
    random.Random(1).shuffle(jobs)  # interleave judges so one slow route does not serialize
    print("grade jobs", len(jobs))

    def grade(job):
        r, j = job
        p = probes[r["probe_id"]]
        g = llm.ask_json(
            j,
            rp.GRADE_PROMPT.format(
                question=r["question"],
                key=p["atom_statement"],
                quals=", ".join(p["atom_qualifiers"]) or "none",
                core=p.get("expected_core", ""),
                ptype="adversarial" if r["adversarial"] else r["style"],
                answer=(r["answer"] or "")[:6000],
            ),
            tag=f"grade|{j}|{key(r)}",
            max_tokens=1200,
            repair_with="claude-haiku-4-5",
        )
        v = g.get("verdict") if isinstance(g, dict) else None
        if v not in rp.SCORE:
            v = None
        rec = {
            "key": key(r),
            "judge": j,
            "verdict": v,
            "reason": (g.get("reason") if isinstance(g, dict) else None),
        }
        with lock:
            with gp.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        return rec

    import threading

    lock = threading.Lock()
    res = llm.pmap(grade, jobs, workers=workers, desc="grading")
    bad = sum(1 for x in res if not isinstance(x, dict) or not x.get("verdict"))
    print("graded", len(res), "bad", bad, llm.stats())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["sample", "repeat", "grade"])
    ap.add_argument("--per-model", type=int, default=90)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--judges", default=",".join(JUDGES))
    a = ap.parse_args()
    if a.step == "sample":
        do_sample(a.per_model)
    elif a.step == "repeat":
        do_repeat(a.workers)
    else:
        do_grade(a.workers, a.judges.split(","))
