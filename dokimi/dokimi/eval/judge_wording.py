# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Wording sensitivity of the judge: does a meaning-preserving rewrite move it?

The judge-validity gate measures one prompt. A judge whose accuracy on a
degradation holds only for that exact wording has not learned the check, it has
been tuned into it — and the next unrelated prompt edit undoes the fix without
any test noticing. Measured on Haiku with the #52 prompt: pooled accuracy moved
by about as much across six rewrites as across three re-draws of the identical
prompt, but ``wrong_numbers`` ranged 0.47-0.72 across rewrites against 0.64-0.74
across re-draws, and a single persona line was enough to take it to the bottom.

A variant is flagged FRAGILE when some rewrite moves it on the *same pairs*:
each rewrite is compared with the original prompt pair by pair (two-sided sign
test on the per-pair outcome), Holm-adjusted over every rewrite and variant
tested, so ``--strict`` fails a judge with no wording effect at most 5% of the
time. The first version compared the range across all conditions with the
range across re-draws; with more wordings than draws that range grows from the
count alone, and identical prompts reassigned to the two roles were flagged on
about 55% of simulated runs at the defaults. The re-draws are still shown as a noise floor
next to the spread, but they no longer decide the flag.

The rewrites are mechanical edits of :data:`~dokimi.eval.judge.JUDGE_PROMPT`
rather than stored paraphrases, so they cannot drift from the prompt they test:
each must leave every placeholder and the output format untouched, and one that
no longer applies (its anchor text was edited away) raises instead of silently
measuring the original prompt twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from dokimi.eval.binary_stats import exact_binomial_two_sided
from dokimi.eval.degrade import NEUTRAL_VARIANTS
from dokimi.eval.judge_validity import Scorer, ValidityReport, evaluate_pairs
from dokimi.eval.stats import adjust_pvalues

_PLACEHOLDERS = ("__QUESTION__", "__ANSWER__", "__CONTEXT__", "__LIBRARY_NAME__")
_OUTPUT_MARK = "**Output format"

#: Family-wise level of the paired wording test.
ALPHA = 0.05

#: Spread below this is never flagged: on 20 pairs one pair is 0.05.
MIN_FLAGGED_SPREAD = 0.10


def _criteria_split(prompt: str) -> tuple:
    start = prompt.index("**Evaluation criteria:**")
    end = prompt.index("**Score anchors") if "**Score anchors" in prompt else prompt.index("**Instructions:**")
    return prompt[:start], prompt[start:end], prompt[end:]


def _persona(prompt: str) -> str:
    return "You are a senior engineer reviewing answers written for your team.\n" + prompt


def _plain_headings(prompt: str) -> str:
    head, tail = prompt.split(_OUTPUT_MARK, 1)
    return head.replace("**", "") + _OUTPUT_MARK + tail


def _reorder_dimensions(prompt: str) -> str:
    head, body, tail = _criteria_split(prompt)
    title, *items = re.split(r"\n(?=\d\. \*\*)", body)
    if len(items) < 2:
        raise ValueError("reorder_dimensions: numbered criteria not found")
    items = [re.sub(r"^\d\.", f"{i}.", item) for i, item in enumerate(reversed(items), 1)]
    # The last item carried the block's trailing blank line; keep the spacing stable.
    items = [item.rstrip("\n") for item in items]
    return head + title + "\n" + "\n".join(items) + "\n" + tail


def _instructions_reversed(prompt: str) -> str:
    head, rest = prompt.split("**Instructions:**\n", 1)
    block, tail = rest.split("\n\n", 1)
    bullets = re.split(r"\n(?=- )", block)
    if len(bullets) < 2:
        raise ValueError("instructions_reversed: instruction bullets not found")
    return head + "**Instructions:**\n" + "\n".join(reversed(bullets)) + "\n\n" + tail


#: Name -> rewrite of the judge prompt. Each keeps the meaning and changes the
#: surface: framing, typography, order of the rubric, order of the instructions.
WORDINGS: Dict[str, Callable[[str], str]] = {
    "persona": _persona,
    "plain_headings": _plain_headings,
    "reorder_dimensions": _reorder_dimensions,
    "instructions_reversed": _instructions_reversed,
}


def reword(prompt: str, name: str) -> str:
    """Apply one rewrite, checking it changed the text and kept the contract."""
    out = WORDINGS[name](prompt)
    if out == prompt:
        raise ValueError(f"wording '{name}' left the prompt unchanged")
    for token in _PLACEHOLDERS:
        if prompt.count(token) != out.count(token):
            raise ValueError(f"wording '{name}' changed the {token} placeholders")
    if prompt.split(_OUTPUT_MARK, 1)[1] != out.split(_OUTPUT_MARK, 1)[1]:
        raise ValueError(f"wording '{name}' changed the output format")
    return out


@dataclass
class WordingReport:
    """Per-variant accuracy of one judge under the original prompt, re-draws and rewrites."""

    judge: str
    #: Condition name -> report. ``original`` and ``redraw-N`` are the noise
    #: floor; every other key is a rewrite from :data:`WORDINGS`.
    reports: Dict[str, ValidityReport] = field(default_factory=dict)

    def _accuracy(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for cond, rep in self.reports.items():
            for variant, v in rep.per_variant().items():
                key = "unmoved_rate" if v["neutral"] else "gate_accuracy"
                out.setdefault(variant, {})[cond] = v[key]
        return out

    def _paired_p(self, variant: str, cond: str) -> Optional[float]:
        """Sign test of *cond* against ``original`` over the pairs both scored.

        The per-pair outcome is the gate's (accuracy, or unmoved for a neutral
        variant; an errored pair counts 0). Under "the wording does not matter"
        the two are exchangeable draws on each pair, so the test is exact.
        """
        def outcomes(rep: ValidityReport) -> Dict[tuple, float]:
            return {
                (r.base_id, r.arm): (r.unmoved if r.neutral else r.correct) or 0.0
                for r in rep.rows if r.variant == variant
            }
        a, b = outcomes(self.reports["original"]), outcomes(self.reports[cond])
        diffs = [b[k] - a[k] for k in a.keys() & b.keys()]
        up, down = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
        return exact_binomial_two_sided(up, up + down)

    def summary(self) -> Dict[str, Any]:
        floor = [c for c in self.reports if c == "original" or c.startswith("redraw")]
        rewrites = [c for c in self.reports if c not in floor]
        acc = self._accuracy()
        tests = [(v, c) for v in sorted(acc) for c in rewrites if c in acc[v] and "original" in acc[v]]
        raw = [self._paired_p(v, c) for v, c in tests]
        holm = dict(zip(tests, adjust_pvalues(raw, method="holm", rounded=False), strict=True))
        # A condition with no scored pair (none built, or every call errored) was
        # not measured: "no rewrite moves anything" would be read off zeros, and an
        # errored pair counts 0 in the sign test, so nothing is flagged either.
        unmeasured = [c for c, r in self.reports.items() if not r.scored]
        measured = bool(self.reports) and not unmeasured
        rows = {}
        for variant, by in sorted(acc.items()):
            noise = [by[c] for c in floor if c in by]
            words = [by[c] for c in rewrites if c in by]
            noise_spread = max(noise) - min(noise) if len(noise) > 1 else 0.0
            all_vals = noise + words
            spread = max(all_vals) - min(all_vals) if all_vals else 0.0
            worst = min(rewrites, key=lambda c: by.get(c, 1.0)) if rewrites else None
            ps = [p for c in rewrites if (p := holm.get((variant, c))) is not None]
            p_min = min(ps) if ps else None
            rows[variant] = {
                "neutral": variant in NEUTRAL_VARIANTS,
                "by_condition": by,
                "noise_spread": noise_spread,
                "spread": spread,
                "worst_wording": worst,
                "p_holm": None if p_min is None else round(p_min, 4),
                "fragile": measured and spread >= MIN_FLAGGED_SPREAD and p_min is not None and p_min < ALPHA,
            }
        pooled = {c: r.summary()["accuracy"] for c, r in self.reports.items()}
        return {
            "judge": self.judge,
            "conditions": list(self.reports),
            "pooled_accuracy": pooled,
            "n_errors": {c: len(r.errors) for c, r in self.reports.items()},
            "unmeasured_conditions": unmeasured,
            "measured": measured,
            "per_variant": rows,
            "fragile_variants": sorted(v for v, r in rows.items() if r["fragile"]),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary(), "reports": {c: r.to_dict() for c, r in self.reports.items()}}

    def format_summary(self) -> str:
        s = self.summary()
        conds = s["conditions"]
        short = {c: c[:10] for c in conds}
        lines = [
            f"Judge wording sensitivity — {s['judge']}",
            "  noise floor: original prompt + re-draws on the same pairs; every other column is a rewrite",
            "",
            f"  {'degradation':20s} " + " ".join(f"{short[c]:>10s}" for c in conds)
            + f" {'noise':>6s} {'spread':>6s}",
        ]
        for variant, r in s["per_variant"].items():
            vals = " ".join(
                f"{r['by_condition'][c]:10.2f}" if c in r["by_condition"] else f"{'-':>10s}" for c in conds
            )
            flag = f"  FRAGILE (worst: {r['worst_wording']})" if r["fragile"] else ""
            label = variant + ("*" if r["neutral"] else "")
            lines.append(f"  {label:20s} {vals} {r['noise_spread']:6.2f} {r['spread']:6.2f}{flag}")
        lines.append(
            f"  {'pooled (graded)':20s} " + " ".join(f"{s['pooled_accuracy'][c]:10.2f}" for c in conds)
        )
        if any(s["n_errors"].values()):
            lines.append(
                f"  {'errored pairs':20s} " + " ".join(f"{s['n_errors'][c]:10d}" for c in conds)
            )
        lines.append("")
        lines.append("  * neutral variant: column is unmoved rate, not accuracy")
        if not s["measured"]:
            lines.append(
                "  NOT MEASURED — no pair scored under: "
                + (", ".join(s["unmeasured_conditions"]) or "any condition")
            )
        if s["fragile_variants"]:
            lines.append(
                "  FRAGILE — a rewrite moves these on the same pairs (paired sign test, Holm): "
                + ", ".join(s["fragile_variants"])
            )
        elif s["measured"]:
            lines.append("  No rewrite moves any variant on the same pairs (paired sign test, Holm).")
        return "\n".join(lines)


def run_wording(
    pairs: Sequence[Dict[str, Any]],
    scorer_for: Callable[[Optional[str], int], Scorer],
    base_prompt: str,
    *,
    judge: str = "unknown",
    wordings: Optional[Sequence[str]] = None,
    redraws: int = 1,
    min_accuracy: float = 0.8,
    seed: int = 0,
    concurrency: int = 1,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> WordingReport:
    """Score the same pairs under the original prompt, ``redraws`` re-draws, and each rewrite.

    ``scorer_for(prompt_template, draw)`` returns a scorer; ``None`` means the
    judge's own prompt.
    """
    conditions: List[tuple] = [("original", None, 0)]
    conditions += [(f"redraw-{d}", None, d) for d in range(1, redraws + 1)]
    conditions += [(name, reword(base_prompt, name), 0) for name in (wordings or WORDINGS)]
    report = WordingReport(judge=judge)
    for name, template, draw in conditions:
        cb = (lambda done, total, _n=name: progress(_n, done, total)) if progress else None
        report.reports[name] = evaluate_pairs(
            scorer_for(template, draw), pairs, judge=judge, min_accuracy=min_accuracy,
            seed=seed, concurrency=concurrency, progress=cb,
        )
    return report
