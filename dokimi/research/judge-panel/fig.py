# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Dot plot: placebo-gated added share per answer model, one dot per judge, panel rules marked."""

import csv
import sys
from pathlib import Path

T = Path(sys.argv[1])
out = Path(sys.argv[2])
mode = sys.argv[3] if len(sys.argv) > 3 else "lenient"
MODELS = ["claude-opus-5", "gpt-5.6-luna", "claude-haiku-4-5", "grok-4.6", "nemotron-nano-12b"]
COL = {
    "gpt-5.6-luna": "#d1495b",
    "claude-sonnet-5": "#c47a2c",
    "gpt-6-luna": "#8e44ad",
    "grok-4.6": "#2a9d8f",
    "kimi-k3": "#3b6fb6",
    "deepseek-v3.2": "#6c757d",
    "glm-5": "#7fb069",
    "qwen3-vl-235b": "#e9a03b",
}
pj = [r for r in csv.DictReader((T / "per_judge_headline.csv").open()) if r["mode"] == mode]
ag = {
    (r["rule"], r["answer_model"]): float(r["gated"])
    for r in csv.DictReader((T / "aggregation_rules.csv").open())
}
W, H, L, R, TOP, ROW = 760, 70 + 46 * len(MODELS) + 60, 170, 30, 40, 46
x0, x1 = 0.0, 0.6


def X(v):
    return L + (W - L - R) * (v - x0) / (x1 - x0)


s = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
    f'<rect width="{W}" height="{H}" fill="white"/>',
    f'<text x="{L}" y="20" font-size="13" font-weight="bold">Placebo-gated added share ({mode}) by judge</text>',
]
for t in range(7):
    v = t / 10
    s.append(
        f'<line x1="{X(v)}" y1="{TOP}" x2="{X(v)}" y2="{TOP + ROW * len(MODELS)}" stroke="#e5e5e5"/>'
    )
    s.append(
        f'<text x="{X(v)}" y="{TOP + ROW * len(MODELS) + 15}" text-anchor="middle" fill="#555">{v:.1f}</text>'
    )
for i, m in enumerate(MODELS):
    y = TOP + ROW * i + ROW / 2
    s.append(f'<text x="{L - 10}" y="{y + 4}" text-anchor="end">{m}</text>')
    vals = [(r["judge"], float(r["gated"])) for r in pj if r["answer_model"] == m]
    lo, hi = min(v for _, v in vals), max(v for _, v in vals)
    s.append(
        f'<line x1="{X(lo)}" y1="{y}" x2="{X(hi)}" y2="{y}" stroke="#bbb" stroke-width="6" stroke-linecap="round"/>'
    )
    for k, (j, v) in enumerate(vals):
        dy = (k - len(vals) / 2) * 2.2
        shape = (
            f'<rect x="{X(v) - 5}" y="{y + dy - 5}" width="10" height="10" fill="{COL[j]}" stroke="black"/>'
            if j == "gpt-5.6-luna"
            else f'<circle cx="{X(v)}" cy="{y + dy}" r="5" fill="{COL[j]}" fill-opacity="0.85"/>'
        )
        s.append(shape)
    if mode == "lenient":
        v = ag[("majority", m)]
        s.append(f'<path d="M{X(v)} {y - 14} l-6 -9 h12 z" fill="black"/>')
ly = TOP + ROW * len(MODELS) + 32
lx = 20
for j, c in COL.items():
    mark = "square = original judge" if j == "gpt-5.6-luna" else ""
    s.append(
        f'<circle cx="{lx}" cy="{ly}" r="5" fill="{c}"/><text x="{lx + 8}" y="{ly + 4}">{j}</text>'
    )
    lx += 92
if mode == "lenient":
    s.append(
        f'<text x="20" y="{ly + 20}" fill="#333">square: original judge (gpt-5.6-luna); triangle: 7-judge majority; grey bar: judge range. n = 60-62 paired non-reversal probes per model.</text>'
    )
s.append("</svg>")
out.write_text("\n".join(s))
print("wrote", out)
