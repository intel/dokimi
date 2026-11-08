#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Analyse judge-panel grades: per-judge headline numbers, kappa, self-preference, aggregation.

Writes OUT/summary.json and CSV tables in OUT/tables/. Pure stdlib + numpy.
"""

from __future__ import annotations

import csv
import itertools
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "judge-panel-out")
T = OUT / "tables"
T.mkdir(exist_ok=True)
V6 = [
    "correct_complete",
    "correct_missing_qualifier",
    "partially_correct",
    "wrong_reversed",
    "wrong_other",
    "abstain",
]
SCORE = {
    "correct_complete": 1.0,
    "correct_missing_qualifier": 0.7,
    "partially_correct": 0.4,
    "wrong_reversed": 0.0,
    "wrong_other": 0.0,
    "abstain": 0.0,
}
LEN = set(V6[:3])
STR = {"correct_complete"}
FAM = {
    "claude-sonnet-5": "anthropic",
    "gpt-6-luna": "openai",
    "gpt-5.6-luna": "openai",
    "grok-4.6": "xai",
    "kimi-k3": "moonshot",
    "deepseek-v3.2": "deepseek",
    "glm-5": "zai",
    "qwen3-vl-235b": "alibaba",
}
AFAM = {
    "claude-opus-5": "anthropic",
    "claude-haiku-4-5": "anthropic",
    "gpt-5.6-luna": "openai",
    "grok-4.6": "xai",
    "nemotron-nano-12b": "nvidia",
}
ORIG = "gpt-5.6-luna"

rows = {}
for f in ("sample.jsonl", "repeats.jsonl"):
    p = OUT / f
    if p.exists():
        for l in p.open():
            r = json.loads(l)
            rows[f"{r['probe_id']}|{r['arm']}|{r['model']}"] = r
V = defaultdict(dict)  # key -> judge -> verdict
for k, r in rows.items():
    if r.get("verdict") in SCORE:
        V[k][ORIG] = r["verdict"]
for l in (OUT / "grades.jsonl").open():
    g = json.loads(l)
    if g.get("verdict") in SCORE and g["key"] in rows:
        V[g["key"]][g["judge"]] = g["verdict"]
JUDGES = [ORIG] + sorted({j for d in V.values() for j in d} - {ORIG})
NEW = [j for j in JUDGES if j != ORIG]
MODELS = list(AFAM)
cells = defaultdict(dict)  # (probe, model) -> arm -> key
for k, r in rows.items():
    cells[(r["probe_id"], r["model"])][r["arm"]] = k


def wcsv(name, header, data):
    with (T / name).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(data)


# ---------- generic "rater" = function key -> verdict-or-binary ----------
def headline(get, model, good, gated_needs_placebo=True):
    """get(key) -> bool|None (correct under `good`). Returns dict of shares over non-adversarial."""
    t = Counter()
    n = 0
    nf = Counter()
    nn = 0
    for (_p, m), arms in cells.items():
        if m != model or rows[arms["no_skill"]]["adversarial"]:
            continue
        b, w, pl = get(arms["no_skill"]), get(arms["full"]), get(arms["placebo"])
        if None in (b, w, pl):
            continue
        n += 1
        t["known"] += b and w
        t["degraded"] += b and not w
        t["added"] += w and not b
        t["gated"] += w and not b and not pl
        if "no_skill_repeat" in arms:
            rr = get(arms["no_skill_repeat"])
            if rr is not None:
                nn += 1
                nf["nf_added"] += rr and not b
                nf["nf_degraded"] += b and not rr
    out = {k: t[k] / n if n else None for k in ("added", "gated", "known", "degraded")}
    out["n"] = n
    out["nf_added"] = nf["nf_added"] / nn if nn else None
    out["nf_degraded"] = nf["nf_degraded"] / nn if nn else None
    out["nf_n"] = nn
    return out


def judge_get(j, good):
    return lambda k: (V[k][j] in good) if j in V[k] else None


summary = {
    "judges": JUDGES,
    "n_rows": len(rows),
    "coverage": {j: sum(j in d for d in V.values()) for j in JUDGES},
}

# ---------- 1. per-judge headline numbers ----------
hl = {}
tab = []
for mode, good in (("lenient", LEN), ("strict", STR)):
    for m in MODELS:
        for j in JUDGES:
            h = headline(judge_get(j, good), m, good)
            hl[(mode, m, j)] = h
            tab.append(
                [
                    mode,
                    m,
                    j,
                    h["n"],
                    *(
                        round(h[x], 3) if h[x] is not None else ""
                        for x in ("added", "gated", "known", "degraded", "nf_added", "nf_degraded")
                    ),
                ]
            )
wcsv(
    "per_judge_headline.csv",
    [
        "mode",
        "answer_model",
        "judge",
        "n",
        "added",
        "gated",
        "known",
        "degraded",
        "noise_added",
        "noise_degraded",
    ],
    tab,
)

# range across judges per (mode, model)
rng = []
for mode in ("lenient", "strict"):
    for m in MODELS:
        for x in ("added", "gated", "known", "degraded"):
            vals = [hl[(mode, m, j)][x] for j in JUDGES if hl[(mode, m, j)][x] is not None]
            rng.append(
                [
                    mode,
                    m,
                    x,
                    round(min(vals), 3),
                    round(max(vals), 3),
                    round(float(np.median(vals)), 3),
                    round(hl[(mode, m, ORIG)][x], 3),
                ]
            )
wcsv("headline_range.csv", ["mode", "answer_model", "metric", "min", "max", "median", "orig"], rng)

# model ranking per judge (gated lenient) -> does the order change?
rank = {}
for mode in ("lenient", "strict"):
    for j in JUDGES:
        order = sorted(MODELS, key=lambda m: -(hl[(mode, m, j)]["gated"] or 0))
        rank[f"{mode}|{j}"] = order
summary["gated_rank_by_judge"] = rank


# ---------- 2. pairwise agreement ----------
def kappa(a, b, labels):
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[l] * cb[l] for l in labels) / n / n
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


kt = []
kap = {}
for j1, j2 in itertools.combinations(JUDGES, 2):
    ks = [k for k in V if j1 in V[k] and j2 in V[k]]
    a6 = [V[k][j1] for k in ks]
    b6 = [V[k][j2] for k in ks]
    al = [x in LEN for x in a6]
    bl = [x in LEN for x in b6]
    as_ = [x in STR for x in a6]
    bs = [x in STR for x in b6]
    r = [
        j1,
        j2,
        len(ks),
        round(kappa(a6, b6, V6), 3),
        round(kappa(al, bl, [True, False]), 3),
        round(kappa(as_, bs, [True, False]), 3),
        round(sum(x == y for x, y in zip(al, bl, strict=True)) / len(ks), 3),
    ]
    kap[(j1, j2)] = kap[(j2, j1)] = r[4]
    kt.append(r)
wcsv(
    "pairwise_kappa.csv",
    ["judge_a", "judge_b", "n", "kappa_6way", "kappa_lenient", "kappa_strict", "agree_lenient"],
    kt,
)
summary["mean_kappa_lenient"] = {
    j: round(float(np.mean([kap[(j, o)] for o in JUDGES if o != j])), 3) for j in JUDGES
}

# leniency of each judge: overall lenient / strict rate
summary["judge_rates"] = {
    j: {
        "lenient": round(np.mean([V[k][j] in LEN for k in V if j in V[k]]), 3),
        "strict": round(np.mean([V[k][j] in STR for k in V if j in V[k]]), 3),
        "dist": dict(Counter(V[k][j] for k in V if j in V[k])),
    }
    for j in JUDGES
}

# ---------- 3. self-preference ----------
# For judge j and answer model m: j's lenient (strict) rate on m minus mean rate of the
# other judges on the same keys. Same-family pairs vs other pairs.
sp = []
for j in JUDGES:
    for m in MODELS:
        for mode, good in (("lenient", LEN), ("strict", STR)):
            ks = [k for k in V if rows[k]["model"] == m and j in V[k] and len(V[k]) >= 4]
            if not ks:
                continue
            own = np.mean([V[k][j] in good for k in ks])
            oth = np.mean([np.mean([V[k][o] in good for o in V[k] if o != j]) for k in ks])
            rel = "self" if j == m else ("family" if FAM[j] == AFAM[m] else "other")
            sp.append([j, m, mode, rel, len(ks), round(own, 3), round(oth, 3), round(own - oth, 3)])
wcsv(
    "self_preference.csv",
    ["judge", "answer_model", "mode", "relation", "n", "judge_rate", "others_rate", "delta"],
    sp,
)
# pooled: per judge, delta on own family minus mean delta on other families
pool = {}
for mode in ("lenient", "strict"):
    for j in JUDGES:
        same = [r[7] for r in sp if r[0] == j and r[2] == mode and r[3] in ("self", "family")]
        oth = [r[7] for r in sp if r[0] == j and r[2] == mode and r[3] == "other"]
        if same:
            pool[f"{mode}|{j}"] = {
                "same_family_delta": round(float(np.mean(same)), 3),
                "other_delta": round(float(np.mean(oth)), 3),
                "self_preference": round(float(np.mean(same) - np.mean(oth)), 3),
            }
summary["self_preference"] = pool


# ---------- 4. aggregation rules over the panel ----------
def dawid_skene(keys, judges, iters=50):
    """Binary Dawid-Skene on lenient labels. Returns key -> P(correct), judge -> (sens, spec)."""
    lab = {k: {j: V[k][j] in LEN for j in judges if j in V[k]} for k in keys}
    p = {k: np.mean(list(d.values())) if d else 0.5 for k, d in lab.items()}
    for _ in range(iters):
        sens, spec = {}, {}
        for j in judges:
            num1 = sum(p[k] * d[j] for k, d in lab.items() if j in d)
            den1 = sum(p[k] for k, d in lab.items() if j in d)
            num0 = sum((1 - p[k]) * (not d[j]) for k, d in lab.items() if j in d)
            den0 = sum((1 - p[k]) for k, d in lab.items() if j in d)
            sens[j] = min(max((num1 + 1) / (den1 + 2), 0.01), 0.99)
            spec[j] = min(max((num0 + 1) / (den0 + 2), 0.01), 0.99)
        prior = np.mean(list(p.values()))
        for k, d in lab.items():
            l1, l0 = np.log(prior), np.log(1 - prior)
            for j, y in d.items():
                l1 += np.log(sens[j] if y else 1 - sens[j])
                l0 += np.log(1 - spec[j] if y else spec[j])
            p[k] = 1 / (1 + np.exp(l0 - l1))
    return p, sens, spec


def rules(judges, keys):
    """Return {rule_name: get(key)->bool|None} for lenient-binary panel decisions."""
    ds_p, sens, spec = dawid_skene(keys, judges)

    def votes(k):
        return [V[k][j] for j in judges if j in V[k]]

    def mk(f):
        def g(k):
            v = votes(k)
            return f(v, k) if len(v) >= max(1, len(judges) // 2 + 1) else None

        return g

    out = {
        "majority": mk(lambda v, k: sum(x in LEN for x in v) > len(v) / 2),
        "mean_score>=0.4": mk(lambda v, k: np.mean([SCORE[x] for x in v]) >= 0.4),
        "median_score>=0.4": mk(lambda v, k: np.median([SCORE[x] for x in v]) >= 0.4),
        "unanimous": mk(lambda v, k: all(x in LEN for x in v)),
        "any_correct": mk(lambda v, k: any(x in LEN for x in v)),
        "dawid_skene": mk(lambda v, k: ds_p[k] >= 0.5),
        "majority_strict": mk(lambda v, k: sum(x in STR for x in v) > len(v) / 2),
    }
    return out, sens, spec


allkeys = list(V)
panel_rules, sens, spec = rules(NEW, allkeys)
summary["dawid_skene_new_judges"] = {
    j: {"sens": round(sens[j], 3), "spec": round(spec[j], 3)} for j in NEW
}
agg = []
for name, g in panel_rules.items():
    for m in MODELS:
        h = headline(g, m, None)
        agg.append(
            [
                name,
                m,
                h["n"],
                *(
                    round(h[x], 3) if h[x] is not None else ""
                    for x in ("added", "gated", "known", "degraded", "nf_added", "nf_degraded")
                ),
            ]
        )
wcsv(
    "aggregation_rules.csv",
    [
        "rule",
        "answer_model",
        "n",
        "added",
        "gated",
        "known",
        "degraded",
        "noise_added",
        "noise_degraded",
    ],
    agg,
)

# agreement of each single judge and each rule with the DS consensus (on NEW judges)
ref = panel_rules["dawid_skene"]
agree = []
for j in JUDGES:
    ks = [k for k in allkeys if j in V[k] and ref(k) is not None]
    agree.append(
        ["judge:" + j, len(ks), round(np.mean([(V[k][j] in LEN) == ref(k) for k in ks]), 3)]
    )
for name, g in panel_rules.items():
    ks = [k for k in allkeys if g(k) is not None and ref(k) is not None]
    agree.append(["rule:" + name, len(ks), round(np.mean([g(k) == ref(k) for k in ks]), 3)])
wcsv("agreement_with_ds.csv", ["rater", "n", "agree_with_ds_consensus"], agree)

# ---------- 5. how many judges are enough ----------
# For panel size s, sample random subsets of NEW judges; majority vote (ties -> DS of subset is
# overkill; ties broken toward 'wrong'). Measure (a) agreement with full 7-judge DS consensus,
# (b) spread of gated-added share across subsets (max-min over models, averaged).
rnd = random.Random(3)
sz = []
for s in range(1, len(NEW) + 1):
    subs = list(itertools.combinations(NEW, s))
    if len(subs) > 35:
        subs = rnd.sample(subs, 35)
    accs, spreads = [], defaultdict(list)
    for sub in subs:

        def g(k, sub=sub):
            v = [V[k][j] in LEN for j in sub if j in V[k]]
            return (sum(v) > len(v) / 2) if v else None

        ks = [k for k in allkeys if g(k) is not None and ref(k) is not None]
        accs.append(np.mean([g(k) == ref(k) for k in ks]))
        for m in MODELS:
            spreads[m].append(headline(g, m, None)["gated"])
    sd = np.mean([np.std(v) for v in spreads.values()])
    rg = np.mean([max(v) - min(v) for v in spreads.values()])
    sz.append(
        [
            s,
            len(subs),
            round(float(np.mean(accs)), 3),
            round(float(np.min(accs)), 3),
            round(float(sd), 4),
            round(float(rg), 3),
        ]
    )
wcsv(
    "panel_size.csv",
    [
        "panel_size",
        "subsets",
        "mean_agree_with_full_ds",
        "worst_agree",
        "sd_gated_added_across_subsets",
        "range_gated_added_across_subsets",
    ],
    sz,
)

# ---------- 6. which arms/probe types do judges disagree on ----------
dis = []
for by in ("arm", "style", "adversarial", "model"):
    grp = defaultdict(list)
    for k in allkeys:
        v = [V[k][j] in LEN for j in NEW if j in V[k]]
        if len(v) >= 5:
            frac = sum(v) / len(v)
            grp[str(rows[k][by])].append(min(frac, 1 - frac) > 0)  # any disagreement
    for gname, xs in sorted(grp.items()):
        dis.append([by, gname, len(xs), round(float(np.mean(xs)), 3)])
wcsv("disagreement_by_group.csv", ["by", "group", "n", "share_items_not_unanimous"], dis)

(OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
print(json.dumps({k: summary[k] for k in ("n_rows", "coverage", "mean_kappa_lenient")}, indent=1))
for f in sorted(T.glob("*.csv")):
    print("==", f.name)
    print(f.read_text()[:2500])
