# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Golden output of :func:`generate_section_report`, one case per section branch.

Each case renders a whole section and compares it byte for byte with
its entry in ``tests/fixtures/model_compare_sections.json``, so a refactor of the section
builder that moves a single character fails here. Between them the cases reach
every section and branch: treatment and baseline-only runs, a run with no
treatment beside one with, one scored arm and two, resource rows with and
without a cost summary, significance on and off (no SciPy), both corrections,
difficulty buckets a run is missing from, replicate pooling with a label clash
and a run left out of ``run_ids``, and an empty section.

To re-record after an intended output change::

    DOKIMI_UPDATE_GOLDEN=1 pytest tests/test_model_compare_sections.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi.report import model_compare
from dokimi.report.model_compare import generate_section_report

GOLDEN = Path(__file__).parent / "fixtures" / "model_compare_sections.json"

_DIFFICULTIES = ["easy", "medium", "hard", 1, "beginner", "advanced", "intermediate", "expert"]


def _run(
    scores: list[tuple],
    arm: str | list[str] = "with-context",
    difficulties=None,
    baseline_arm: str = "baseline",
    **meta,
) -> dict:
    """An ``arms.v1``-shaped artifact; *scores* holds (baseline, treatment) per question.

    *arm* may be a list: question i is scored in ``arm[i % len(arm)]``.

    A treatment of ``...`` leaves the arm out of the record (a baseline-only question);
    ``None`` keeps the arm with no usable score.
    """
    difficulties = difficulties or _DIFFICULTIES
    evaluations = []
    for i, (base, treat) in enumerate(scores):
        cond: dict = {baseline_arm: {"aggregate": base}}
        if treat is not ...:
            cond[arm[i % len(arm)] if isinstance(arm, list) else arm] = {"aggregate": treat}
        evaluations.append(
            {
                "question_id": f"q{i}",
                "question_text": f"question {i}",
                "difficulty": difficulties[i % len(difficulties)],
                "scores": cond,
            }
        )
    return {
        "baseline_arm": baseline_arm,
        "model": "m",
        "provider": "p",
        "evaluations": evaluations,
        **meta,
    }


def _cost(**fields) -> dict:
    return {"baseline": fields}


_FULL_COST = _cost(
    total_cost_usd=0.12345,
    cost_known_n=3,
    total_llm_calls=8,
    prompt_tokens=12345,
    completion_tokens=678,
    total_tokens=13023,
    mean_latency_sec=1.234,
    cache_hit_ratio=0.25,
)

# Varying deltas so the t-test runs; one unpaired question and one with no baseline.
_A = [(50, 70), (60, 70), (40, 80), (70, 60), (55, 75), (65, 90), (30, 45), (80, None), (None, 60)]
# Constant +10 deltas: the zero-variance guard leaves the p-values N/A.
_B = [(50, 60), (60, 70), (40, 50), (70, 80), (55, 65), (65, 75), (30, 40), (80, 90), (20, 30)]
_BASE_ONLY = [(b, ...) for b, _ in _A]
# A raw t-test p of 0.024 and Wilcoxon 0.047: significant alone, not once Holm-adjusted over 3 runs.
_H = [(50, 58), (50, 50), (50, 64), (50, 47), (50, 59), (50, 52), (50, 61), (50, 56)]
# t-test p 0.038 but Wilcoxon 0.0625: adjusted p stays below 0.05, the Wilcoxon clause says No.
_W = [(50, 47), (50, 57), (50, 60), (50, 58), (50, 55), (50, 54)]
# Zeros are scores: a 0 in either arm, and a bucket whose only treatment score is 0.
_Z = [(0, 40), (30, 0), (50, 60), (40, 55), (0, 0), (20, 35), (45, 50), (10, 25)]
# Exactly one question without a usable pair.
_U = [(50, 60), (60, 75), (40, 45), (70, 80), (55, 70), (65, 70), (30, 50), (80, None)]


def _cases() -> dict:
    a = _run(_A, model="alpha", judge_model="judge-j", judge_provider="jp", cost_summary=_FULL_COST)
    b = _run(_B, model="beta", cost_summary=_cost(total_cost_usd=1.5, n=4))
    # Every question "hard" but the one with no baseline score: the run is missing
    # from the other buckets, and its "easy" bucket has no usable score at all.
    lopsided = ["hard"] * 8 + ["easy"]
    return {
        "treatment": ([("a", a), ("b", b)], ["a", "b"], {}),
        "treatment_bh": ([("a", a), ("b", b)], ["a", "b"], {"correction": "bh"}),
        "no_scipy": ([("a", a), ("b", b)], ["a", "b"], {"no_scipy": True}),
        "baseline_only": (
            [
                ("a", _run(_BASE_ONLY, model="alpha")),
                ("b", _run(_BASE_ONLY, model="beta", difficulties=lopsided)),
            ],
            ["a", "b"],
            {},
        ),
        "baseline_only_no_scipy": ([("a", _run(_BASE_ONLY))], ["a"], {"no_scipy": True}),
        "mixed": (
            [
                ("a", _run(_A, model="alpha")),
                ("b", _run(_BASE_ONLY, model="beta", difficulties=lopsided)),
            ],
            ["a", "b"],
            {},
        ),
        "two_arms": (
            [
                ("a", _run(_A, arm="docs", model="alpha")),
                ("b", _run(_B, arm="skill:x", model="beta")),
            ],
            ["a", "b"],
            {},
        ),
        "pinned_arm": (
            [("a", _run(_A, arm="docs", difficulties=["easy"]))],
            ["a"],
            {"treatment_arm": "docs"},
        ),
        "pooled": (
            [
                ("m[n=2]", _run(_B, model="solo")),
                ("m_run1", _run(_A, model="rep", cost_summary=_FULL_COST)),
                ("m_run2", _run(_B, model="rep", cost_summary=_FULL_COST)),
                ("left_out", _run(_A, model="other")),
            ],
            ["m[n=2]", "m_run1", "m_run2"],
            {"pool_replicates": True},
        ),
        "pooled_singletons": (
            [("x", _run(_A, model="x")), ("y", _run(_B, model="y"))],
            ["x", "y"],
            {"pool_replicates": True},
        ),
        "empty": ([], [], {}),
        # The raw verdict is Yes for each of the three, the adjusted one No.
        "verdict_holm_no": (
            [(r, _run(_H, model=r)) for r in ("h1", "h2", "h3")],
            ["h1", "h2", "h3"],
            {},
        ),
        "verdict_wilcoxon_no": ([("w", _run(_W))], ["w"], {}),
        "zero_scores": ([("z", _run(_Z))], ["z"], {}),
        # A baseline-only bucket whose only score is 0 prints 0.0, not a dash.
        "baseline_zero": (
            [("a", _run([(0, ...), (40, ...), (50, ...)], difficulties=["easy", "medium", "hard"]))],
            ["a"],
            {},
        ),
        # One unscored question; a run whose questions span two arms; a cost covering 4 of
        # 5 calls with the count read from ``n``, and one covering all 5.
        "unscored_and_arms": (
            [
                ("u", _run(_U, arm=["docs", "skill:x"], cost_summary=_cost(total_cost_usd=2.0, cost_known_n=4, n=5))),
                ("v", _run(_U, arm="docs", cost_summary=_cost(total_cost_usd=3.0, cost_known_n=5, total_llm_calls=5))),
            ],
            ["u", "v"],
            {},
        ),
        # Replicates that do not pool leave no footnote; a baseline arm that is not "baseline".
        "pooled_singletons_cost": (
            [
                ("x", _run(_A, model="x", cost_summary=_FULL_COST)),
                ("y", _run(_B, model="y", baseline_arm="control", cost_summary={"control": {"total_cost_usd": 0.5}, "baseline": {"total_cost_usd": 9.0}})),
            ],
            ["x", "y"],
            {"pool_replicates": True},
        ),
    }


CASES = _cases()


def _render(name: str, monkeypatch) -> str:
    runs, run_ids, opts = CASES[name]
    opts = dict(opts)
    if opts.pop("no_scipy", False):
        monkeypatch.setattr(model_compare, "HAS_SCIPY", False)
    return "\n".join(generate_section_report(runs, run_ids, "Regular", **opts))


@pytest.mark.parametrize("name", sorted(CASES))
def test_section_report_matches_golden(name, monkeypatch):
    if not model_compare.HAS_SCIPY:
        pytest.skip("the goldens were recorded with SciPy available")
    text = _render(name, monkeypatch)
    if os.environ.get("DOKIMI_UPDATE_GOLDEN"):
        goldens = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        goldens[name] = text
        GOLDEN.write_text(
            json.dumps(goldens, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    assert text == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]


def test_goldens_reach_every_section_branch():
    """The goldens are only a characterization if they hold each branch's marker."""
    texts = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(texts) == set(CASES)
    markers = {
        "treatment": [
            "(3/8)",
            "| **b** | $1.5000 | — |",
            "N/A",
            "Holm-Bonferroni",
            "2 unscored",
            "`with-context`",
            "| tie | 1 |",
            "| beginner | 1 |",
        ],
        "treatment_bh": ["Benjamini-Hochberg"],
        "no_scipy": ["## Overall Summary"],
        "baseline_only": [
            "Report Limitation",
            "a baseline |",
            "### By absolute quality (baseline)",
            "Statistical tests require treatment arm",
            "requires treatment arm.",
        ],
        "mixed": ["— (baseline only)", "(baseline) |", "| — | — |"],
        "two_arms": ["Context arm", "non-baseline treatment arm"],
        "pooled": ["## Replicate Pooling", "m[n=2]'", "pools 2 replicates", "A pooled `[n=k]` row"],
        "empty": ["Common questions evaluated: **0**"],
        "verdict_holm_no": ["| No |", "(Holm-Bonferroni)"],
        "verdict_wilcoxon_no": ["| No |"],
        "zero_scores": ["| beginner | 1 | 0.0 | 0.0 |", "| medium | 1 | 0.0 | -30.0 |"],
        "baseline_zero": ["| easy | 1 | 0.0 |"],
        "unscored_and_arms": ["7 (1 unscored)", "`docs`, `skill:x`", "(4/5)"],
        "pooled_singletons_cost": ["## Resource Usage", "| **y** | $0.5000 |"],
    }
    for name, wanted in markers.items():
        for marker in wanted:
            assert marker in texts[name], (name, marker)
    assert "Statistical Significance" not in texts["no_scipy"]
    assert "Statistical Significance" not in texts["baseline_only_no_scipy"]
    assert "Resource Usage" not in texts["baseline_only"]
    assert "OK Yes" not in texts["verdict_holm_no"] and "OK Yes" not in texts["verdict_wilcoxon_no"]
    assert "pooled `[n=k]` row" not in texts["pooled_singletons_cost"]
    assert "(5/5)" not in texts["unscored_and_arms"]


@pytest.mark.parametrize(
    "p, p_wilcoxon, significant",
    [(0.05, None, False), (0.049, None, True), (0.049, 0.05, False), (0.049, 0.049, True)],
)
def test_adjusted_verdict_is_strictly_below_alpha(monkeypatch, p, p_wilcoxon, significant):
    # A p exactly at alpha is not significant, for the t-test and for the Wilcoxon clause.
    fake = {"p_ttest": p, "p_wilcoxon": p_wilcoxon, "significant": True}
    monkeypatch.setattr(model_compare, "significance_test", lambda *_: dict(fake))
    scores = {"r": [{"with_docs": 60, "without_docs": 50}] * 6}
    sigs, tested = model_compare._adjusted_significance(["r"], scores, "holm")
    assert tested == 1
    assert sigs["r"]["significant_adjusted"] is significant
