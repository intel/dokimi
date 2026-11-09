#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Old vs new judge generations on the same rows: leniency, headline shift, agreement, panels.

Reuses analyze.py's loading and headline() (run with its tables dir redirected), writes
OUT/tables_gen/*.csv and OUT/gen_summary.json.
"""
import importlib.util
import json
import os
from pathlib import Path

HERE = Path(__file__).parent
os.environ["JP_TABLES"] = "tables_gen"  # analyze.py's table dir (T) if run_study() is called: keep it out of tables/
_spec = importlib.util.spec_from_file_location("analyze", HERE / "analyze.py")
an = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(an)
V, rows, cells, headline, wcsv = an.V_ALL, an.rows, an.cells, an.headline, an.wcsv  # all judges
LEN, STR, SCORE, V6, MODELS, OUT = an.LEN, an.STR, an.SCORE, an.V6, an.MODELS, an.OUT

PAIRS = [  # family, old -> new
    ("openai", "gpt-5.6-luna", "gpt-6-luna"),
    ("openai", "gpt-5.6-luna", "gpt-6-sol"),
    ("moonshot", "kimi-k2.5", "kimi-k3"),
    ("anthropic", "claude-haiku-4-5", "claude-sonnet-5"),
    ("anthropic", "claude-sonnet-5", "claude-opus-5-5"),
    ("anthropic", "claude-haiku-4-5", "claude-opus-5-5"),
    ("deepseek", "deepseek-r1", "deepseek-v3.2"),
]
OLD_PANEL = ["gpt-5.6-luna", "kimi-k2.5", "claude-haiku-4-5"]
NEW_PANEL = ["gpt-6-luna", "kimi-k3", "claude-opus-5-5"]
ALL = sorted({j for d in V.values() for j in d})
NEED = sorted({j for _, old, new in PAIRS for j in (old, new)} | set(OLD_PANEL) | set(NEW_PANEL))
if missing := sorted(set(NEED) - set(ALL)):
    raise SystemExit(f"gen.py needs grades from {', '.join(missing)}; grade those judges first")


def kappa(a, b):
    keys = [k for k in V if a in V[k] and b in V[k]]
    n = len(keys)
    if n == 0:
        raise SystemExit(f"no answer graded by both {a} and {b}")
    po = sum(V[k][a] == V[k][b] for k in keys) / n
    pe = sum((sum(V[k][a] == l for k in keys) / n) * (sum(V[k][b] == l for k in keys) / n)
             for l in V6)
    return ((po - pe) / (1 - pe) if pe < 1 else 1.0), n  # as analyze.py: chance agreement 1 is perfect


def lenient_rate(j):
    vs = [d[j] for d in V.values() if j in d]
    return sum(v in LEN for v in vs) / len(vs), sum(v in STR for v in vs) / len(vs), len(vs)


def median(votes):
    r = sorted(votes, key=lambda v: -SCORE[v])
    return r[len(r) // 2]


def panel_get(panel, good):
    def get(k):
        vs = [V[k][j] for j in panel if j in V[k]]
        return (median(vs) in good) if len(vs) == len(panel) else None
    return get


def jget(j, good):
    return lambda k: (V[k][j] in good) if j in V[k] else None


# A pair or panel judge with no complete no_skill/full/placebo triplet for an answer model has a None
# headline. Name it here, before any output is written, instead of failing on the rounding below.
for good in (LEN, STR):
    gets = [(j, jget(j, good)) for j in NEED] + [(f"panel {'+'.join(p)}", panel_get(p, good))
                                                 for p in (OLD_PANEL, NEW_PANEL)]
    for name, get in gets:
        for m in MODELS:
            if headline(get, m, good)["added"] is None:
                raise SystemExit(f"gen.py has no complete no_skill/full/placebo triplet for {m} under {name}; "
                                 "grade it on every arm first")

out = {"judges": ALL, "per_judge": {}, "pairs": [], "panels": {}}
tab = []
for j in ALL:
    le, st, n = lenient_rate(j)
    out["per_judge"][j] = {"lenient_rate": round(le, 4), "strict_rate": round(st, 4), "n": n}
    tab.append([j, n, round(le, 4), round(st, 4)])
wcsv("judge_leniency.csv", ["judge", "n", "lenient_rate", "strict_rate"], tab)

tab, ktab = [], []
for fam, old, new in PAIRS:
    k, n = kappa(old, new)
    row = {"family": fam, "old": old, "new": new, "kappa_old_new": round(k, 3), "n": n,
           "lenient_rate_delta": round(out["per_judge"][new]["lenient_rate"]
                                       - out["per_judge"][old]["lenient_rate"], 4),
           "added": {}}
    for mode, good in (("lenient", LEN), ("strict", STR)):
        for m in MODELS:
            ho, hn = headline(jget(old, good), m, good), headline(jget(new, good), m, good)
            d = None if ho["added"] is None or hn["added"] is None else hn["added"] - ho["added"]
            row["added"][f"{mode}:{m}"] = [ho["added"], hn["added"]]
            tab.append([fam, old, new, mode, m, round(ho["added"], 3), round(hn["added"], 3),
                        round(d, 3) if d is not None else ""])
    out["pairs"].append(row)
    ktab.append([fam, old, new, n, round(k, 3), row["lenient_rate_delta"]])
wcsv("generation_kappa.csv", ["family", "old_judge", "new_judge", "n", "kappa", "lenient_rate_delta"],
     ktab)
wcsv("generation_added_share.csv", ["family", "old_judge", "new_judge", "mode", "answer_model",
                                    "added_old", "added_new", "delta"], tab)

# agreement with the rest of the panel: does a newer judge sit closer to the consensus?
# Pairs with no answer graded by both have no kappa: left out; no partner gives None.
def shared(a, b):
    return any(a in V[k] and b in V[k] for k in V)


tab = []
for j in ALL:
    ks = [kappa(j, o)[0] for o in ALL if o != j and shared(j, o)]
    mk = round(sum(ks) / len(ks), 3) if ks else None
    out["per_judge"][j]["mean_kappa_vs_others"] = mk
    tab.append([j, mk])
wcsv("judge_mean_kappa.csv", ["judge", "mean_kappa_vs_others"], tab)

tab = []
for name, panel in (("old", OLD_PANEL), ("new", NEW_PANEL)):
    out["panels"][name] = {"judges": panel, "added": {}}
    for mode, good in (("lenient", LEN), ("strict", STR)):
        for m in MODELS:
            h = headline(panel_get(panel, good), m, good)
            out["panels"][name]["added"][f"{mode}:{m}"] = h["added"]
            tab.append([name, "+".join(panel), mode, m, h["n"], round(h["added"], 3)])
wcsv("generation_panels.csv", ["panel", "judges", "mode", "answer_model", "n", "added"], tab)
(OUT / "gen_summary.json").write_text(json.dumps(out, indent=1))

print("judge leniency strict mean_kappa")
for j in ALL:
    p = out["per_judge"][j]
    mk = "n/a" if p["mean_kappa_vs_others"] is None else f"{p['mean_kappa_vs_others']:.3f}"
    print(f"  {j:24s} {p['lenient_rate']:.3f} {p['strict_rate']:.3f} {mk}")
print("pairs (kappa old-new, lenient-rate delta, lenient added old->new per answer model)")
for r in out["pairs"]:
    adds = " ".join(f"{k.split(':')[1][:10]}={v[0]:.2f}->{v[1]:.2f}" for k, v in r["added"].items()
                    if k.startswith("lenient"))
    print(f"  {r['old']}->{r['new']}: k={r['kappa_old_new']} dlen={r['lenient_rate_delta']:+.3f} {adds}")
print("panels median, lenient added")
for name, p in out["panels"].items():
    print(" ", name, {k.split(':')[1]: round(v, 3) for k, v in p["added"].items() if k.startswith("lenient")})
    print(" ", name, "strict", {k.split(':')[1]: round(v, 3) for k, v in p["added"].items() if k.startswith("strict")})
