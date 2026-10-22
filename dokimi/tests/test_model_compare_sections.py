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


def _run(scores: list[tuple], arm: str = "with-context", difficulties=None, **meta) -> dict:
    """An ``arms.v1``-shaped artifact; *scores* holds (baseline, treatment) per question.

    A treatment of ``...`` leaves the arm out of the record (a baseline-only question);
    ``None`` keeps the arm with no usable score.
    """
    difficulties = difficulties or _DIFFICULTIES
    evaluations = []
    for i, (base, treat) in enumerate(scores):
        cond: dict = {"baseline": {"aggregate": base}}
        if treat is not ...:
            cond[arm] = {"aggregate": treat}
        evaluations.append(
            {
                "question_id": f"q{i}",
                "question_text": f"question {i}",
                "difficulty": difficulties[i % len(difficulties)],
                "scores": cond,
            }
        )
    return {
        "baseline_arm": "baseline",
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
    }
    for name, wanted in markers.items():
        for marker in wanted:
            assert marker in texts[name], (name, marker)
    assert "Statistical Significance" not in texts["no_scipy"]
    assert "Statistical Significance" not in texts["baseline_only_no_scipy"]
    assert "Resource Usage" not in texts["baseline_only"]
