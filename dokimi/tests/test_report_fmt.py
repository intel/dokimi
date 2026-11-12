# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Golden output of the report renderers on values that test their number cells.

Each case renders a whole report and compares it byte for byte with its entry in
``tests/fixtures/report_fmt.json``. The inputs hold the values the cell formatters treat
apart: ``None``, NaN, an infinity, ``-0.0``, a value that rounds to zero from either side,
a half-way value at the cell's digits, a p under 0.0001 and a cost under a hundredth of a cent.
``report compare`` has its own goldens (``tests/test_model_compare_sections.py``).

To re-record after an intended output change::

    DOKIMI_UPDATE_GOLDEN=1 pytest tests/test_report_fmt.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi.eval.arm_summary import TOOL_COUNTS
from dokimi.report.arms_report import render_arms_report
from dokimi.report.exploration_report import render_exploration_metrics, render_explore_report
from dokimi.report.generator import ReportGenerator
from dokimi.report.plugin_delta_report import render_plugin_delta_report
from dokimi.report.subject_report import render_subject_scorecard
from dokimi.report.task_runs_report import render_task_runs_report

GOLDEN = Path(__file__).parent / "fixtures" / "report_fmt.json"

NAN = float("nan")
INF = float("inf")


def _sig(**fields) -> dict:
    return {"effect": "small", "n": 4, "correction": "holm", **fields}


def _arms() -> dict:
    evaluations = [
        {"question_id": f"q{i}", "question_text": f"question {i}",
         "scores": {arm: {"aggregate": s} for arm, s in zip(("baseline", "docs", "skill", "combo"), row, strict=True)
                    if s is not None}}
        for i, row in enumerate([(72.5, 73.5, None, 0), (60, 99.99, 0.5, 100), (55, -0.0, 12.5, None)])
    ]
    return {
        "library_name": "lib", "model": "m", "provider": "p",
        "arms": ["baseline", "docs", "skill", "combo"], "baseline_arm": "baseline",
        "total_questions": 3, "answers": [], "evaluations": evaluations,
        "summary": {
            "per_arm": {
                "baseline": {"avg_aggregate": 62.25, "n": 3, "errors": 0},
                "docs": {"avg_aggregate": 70.05, "delta_vs_baseline": 7.75, "n": 3,
                         "significance": _sig(delta_mean=7.75, ci_lo=0.04, ci_hi=12.25, cohens_d=NAN,
                                              p=0.00001, p_adjusted=0.00004, significant_adjusted=True)},
                "skill": {"avg_aggregate": None, "delta_vs_baseline": None, "n": 2,
                          "significance": _sig(delta_mean=-0.00001, ci_lo=None, ci_hi=0.003, cohens_d=None,
                                               p=None, p_adjusted=None, significant_adjusted=None)},
                "combo": {"avg_aggregate": -0.0, "delta_vs_baseline": -0.04, "n": 2,
                          "significance": _sig(delta_mean=-0.04, ci_lo=-0.04, ci_hi=0.0, cohens_d=-0.004,
                                               p_ttest=0.05, p_adjusted=0.15, significant_adjusted=False)},
            },
            "interactions": [
                {"arm_a": "docs", "arm_b": "skill", "delta_a": 1.25, "delta_b": -0.0,
                 "additive_expectation": None, "delta_combined": 0.04, "interaction": -2.35,
                 "ci_lo": -INF, "ci_hi": None, "p": 0.12345, "p_adjusted": None,
                 "significant_adjusted": False, "n": 2},
            ],
        },
        "cost_summary": {
            "baseline": {"total_cost_usd": 0.00001, "cost_known_n": 3, "total_llm_calls": 4, "prompt_tokens": 10,
                         "completion_tokens": 5, "mean_latency_sec": 1.2345, "mean_ttft_sec": None,
                         "cache_hit_ratio": 0.125, "n": 4},
            "docs": {"total_cost_usd": None, "mean_latency_sec": NAN, "mean_ttft_sec": 0.0005,
                     "cache_hit_ratio": 0.0, "n": 4},
            "skill": {"total_cost_usd": 0.12345, "cost_known_n": 4, "n": 4, "mean_latency_sec": None,
                      "cache_hit_ratio": None},
        },
        "agentic_usage_summary": {
            "docs": {"n": 4, "tool_use_rate": 0.125, "avg_tool_calls": 2.25, "skill_load_rate": None,
                     "avg_iterations": 1.05, "tool_counts": TOOL_COUNTS},
            "skill": {"n": 4, "tool_use_rate": 1.0, "avg_tool_calls": 0, "skill_load_rate": 0.995,
                      "avg_iterations": -0.0, "tool_counts": TOOL_COUNTS},
        },
        "skill_selection_summary": {
            "skill": {"selection_accuracy": 0.5, "recall": None, "precision": 1.0, "recall_any": 0.005,
                      "multi_load_rate": 0.0, "misroute_rate": 0.125, "false_trigger_rate": NAN,
                      "n_positive": 3, "n_negative": 1, "n": 4, "n_failed": 0},
        },
    }


def _plugin_delta() -> dict:
    def deltas(**fields):
        return {k: {"delta": v} for k, v in fields.items()}

    return {
        "library_name": "lib", "model": "m", "provider": "p", "harness": "h",
        "arms": ["baseline", "docs", "skill"], "total_questions": 3,
        "score_deltas": {
            "baseline": {"baseline_avg_aggregate": 62.25, "plugin_avg_aggregate": 62.35,
                         "aggregate_delta": 0.1, "n": 3},
            "docs": {"baseline_avg_aggregate": None, "plugin_avg_aggregate": -0.0,
                     "aggregate_delta": -0.04, "n": 2},
            "skill": {"baseline_avg_aggregate": NAN, "plugin_avg_aggregate": 100,
                      "aggregate_delta": None},
        },
        "cost_deltas": {
            "baseline": deltas(total_cost_usd=-0.00001, prompt_tokens=12, completion_tokens=-3.5,
                               total_tokens=None, mean_latency_sec=-0.04),
            "docs": deltas(total_cost_usd=0.12345, prompt_tokens=-0.0, completion_tokens=2.5,
                           total_tokens=8.5, mean_latency_sec=2.25),
            "skill": {"total_cost_usd": 0.5},
        },
        "answer_text_deltas": {
            "baseline": deltas(raw_answer_chars=-120, final_answer_chars=0.5),
            "docs": deltas(raw_answer_chars=None, final_answer_chars=INF),
        },
    }


def _subject() -> dict:
    def cell(ref, added_l, added_s, k0_l, k0_s, dead, unread=0, tokens=0, n_paired=4):
        return {
            "artifact": {"ref": ref}, "model": "m",
            "paired": {"lenient": {"added": added_l, "n_paired": n_paired}, "strict": {"added": added_s}},
            "k0": {"lenient": k0_l, "strict": k0_s},
            "spans": {"frac_carrying_nothing": dead, "tokens_unread": unread, "tokens": tokens},
            "gate": {"status": "pass"},
        }

    return {
        "subject": {"id": "s", "kind": "skill", "ref": "r"}, "baseline": "b",
        "work": {"status": "done", "summary": {"per_harness": {
            "h1": {"pass_rate": 0.12345, "passed": 1, "scored": 8, "errored": 0},
            "h2": {"pass_rate": None, "passed": 0, "scored": 0, "errored": 2},
            "h3": {"pass_rate": 1, "passed": 3, "scored": 3},
        }}},
        "novelty": {"cells": [
            cell("a", 0.12345, 3, 0.0005, -0.0, 0.0, unread=1, tokens=200),
            cell("b", None, 0.5, NAN, 2, None, n_paired="n/a"),
            cell("c", -0.0004, True, 1.0, None, 0.25, unread=5, tokens=100),
        ]},
    }


def _task_runs() -> dict:
    def row(task, harness, passed, cost, reward=None):
        return {"task": task, "harness": harness, "passed": passed, "reward": reward, "returncode": 0,
                "output_dir": None, "metrics": {"cost_usd": cost}}

    a, b, c = "docker-a", "docker-b", "docker-b-skill-discover"
    return {
        "model": "m", "repeats": 1, "baseline_harness": a, "generated_at": "t",
        "tasks": [{"name": "t1", "metadata": {"difficulty": "easy"}}, {"name": "t2"}],
        "results": [
            row("t1", a, True, 0.00001, 1.0), row("t2", a, False, None, 0.5),
            row("t1", b, True, 0.12345, 1), row("t2", b, True, -0.0),
            row("t1", c, False, 0.00005), row("t2", c, False, "free"),
        ],
        "summary": {
            "per_harness": {
                a: {"n": 2, "scored": 2, "passed": 1, "pass_rate": 0.5, "elapsed_sec": 90,
                    "pass_rate_ci": {"lo": 0.0945, "hi": 0.9055},
                    "cost": {"total_cost_usd": 0.00001, "cost_per_pass_usd": 0.00001, "cost_known_n": 1,
                             "completion_tokens": None, "cache_read_tokens": 0}},
                b: {"n": 2, "passed": 2, "pass_rate": 1.0, "elapsed_sec": 0, "skill_invocation_rate": 0.125,
                    "skill_invocation_n": 2,
                    "cost": {"total_cost_usd": 0.12345, "cost_per_pass_usd": 0.061725, "cost_known_n": 2}},
                c: {"n": 2, "passed": 0, "pass_rate": 0.0, "pass_rate_ci": {"lo": 0, "hi": None},
                    "cost": {"total_cost_usd": None, "cost_per_pass_usd": None}},
            },
            "comparisons": {
                b: {"pass_rate_delta": 0.5, "normalized_gain": 1.0, "normalized_change": 0.999,
                    "passed_delta": 1, "operation_count_delta": -2, "cost_delta_usd": 0.12344,
                    "mcnemar": {"paired_pass_rate_delta": 0.5, "ci_lo": -0.00004, "ci_hi": 1.0,
                                "n_gained": 1, "n_lost": 0, "n_both_passed": 1, "n_both_failed": 0,
                                "p_exact": 0.00001, "p_adjusted": None, "significant_adjusted": None,
                                "significant": True, "n": 2}},
                c: {"pass_rate_delta": -0.5, "normalized_gain": -0.0, "normalized_change": None,
                    "passed_delta": -1, "operation_count_delta": 0, "cost_delta_usd": -0.00001,
                    "mcnemar": {"paired_pass_rate_delta": -0.5, "ci_lo": None, "ci_hi": 0.0,
                                "p_exact": 0.5, "p_adjusted": NAN, "significant_adjusted": False, "n": 2}},
            },
            "discovery_gaps": {
                c: {"gap": 1.0, "with_skill_pass_rate": 1.0, "discover_pass_rate": 0.0,
                    "with_skill_invocation_rate": 0.125, "discover_invocation_rate": None},
                "docker-x-skill-discover": {"gap": None, "with_skill_pass_rate": True, "discover_pass_rate": 0.005,
                                            "with_skill_invocation_rate": NAN, "discover_invocation_rate": 0.995},
            },
        },
    }


def _explore() -> dict:
    return {"summary": {
        "per_arm": {
            "baseline": {"n": 3, "avg_file_f1": 0.12345, "avg_line_f1": 0.0005, "avg_citation_validity_rate": 1,
                         "avg_citation_compactness": None},
            "docs": {"n": 3, "avg_file_f1": 0.9995, "avg_line_f1": -0.0, "avg_citation_validity_rate": NAN,
                     "avg_citation_compactness": 0.25},
            "skill": {"n": 0, "avg_file_f1": None},
        },
        "comparisons": {
            "docs": {"baseline_arm": "baseline", "file_f1_delta": 0.87605, "line_f1_delta": -0.0004},
            "skill": {"baseline_arm": "baseline", "file_f1_delta": None, "line_f1_delta": -0.0},
        },
    }}


def _exploration_metrics() -> dict:
    def row(task, ratio, calls):
        return {"task": task, "harness": "h", "metrics": {"exploration_metrics": {
            "pre_edit_tool_calls": calls, "repeated_read_ratio": ratio, "broad_search_count": 2,
            "broad_search_after_subagent_count": None, "main_agent_tokens": 1200, "full_system_tokens": 3.5}}}

    return {"results": [row("t1", 0.3335, 4), row("t2", None, None), row("t3", -0.0, 0), {"task": "t4"}]}


def _generator_report() -> str:
    def ev(qid, category, with_s, without_s):
        delta = None if with_s is None or without_s is None else with_s - without_s
        return {"question_id": qid, "question_text": f"question {qid}", "category": category,
                "with_docs": None if with_s is None else {"aggregate": with_s},
                "without_docs": None if without_s is None else {"aggregate": without_s}, "delta": delta}

    evaluations = [
        ev("q1", "a", 72.25, 72.3), ev("q2", "a", 40, 70), ev("q3", "b", 60.05, 60),
        ev("q4", "b", 50, 52.5), ev("q5", "c", 80, None), ev("q6", "a", 0, 0.04),
        ev("q7", "c", None, 30),
    ]
    questions = [{"id": e["question_id"], "category": e["category"], "source_type": src}
                 for e, src in zip(evaluations, ["generated", "generated", "curated", "curated", "issue", "issue", "solo"],
                                   strict=True)]
    text = ReportGenerator().generate_report({"evaluations": evaluations}, {"questions": questions})
    return text[text.index("## Overall Statistics"):]  # the trust block above is the gate's, not a report cell


CASES = {
    "arms": lambda: render_arms_report(_arms()),
    "plugin_delta": lambda: render_plugin_delta_report(_plugin_delta()),
    "subject": lambda: render_subject_scorecard(_subject()),
    "task_runs": lambda: render_task_runs_report(_task_runs()),
    "explore": lambda: render_explore_report(_explore()),
    "exploration_metrics": lambda: render_exploration_metrics(_exploration_metrics()),
    "generator": _generator_report,
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_renderer_matches_golden(name):
    lines = CASES[name]().split("\n")  # one line per JSON string, so a changed cell is one line of the diff
    if os.environ.get("DOKIMI_UPDATE_GOLDEN"):
        goldens = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        goldens[name] = lines
        GOLDEN.write_text(json.dumps(goldens, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    assert lines == json.loads(GOLDEN.read_text(encoding="utf-8"))[name]


def test_goldens_hold_the_edge_values():
    """The goldens are only a characterization if the cells reach each value the formatters treat apart."""
    texts = {name: "\n".join(lines) for name, lines in json.loads(GOLDEN.read_text(encoding="utf-8")).items()}
    assert set(texts) == set(CASES)
    for name, marks in {
        "arms": ["<0.0001", "+nan", "[-inf, +0.003]", "| -0.00001 |", "$0.0000 (3/4)", "12.5%", "100%"],
        "plugin_delta": ["$-0.0000", "| -4 |", "| +inf |", "| nan |"],
        "subject": ["0.123 / 3", "n/a / 0.500", "nan / 2", "-0.000 / True", "(<1% unread)", "12.35%"],
        "task_runs": ["<$0.0001", "| $0.0001 |", "[-0.0, +100.0] pp", "| nan |", "$-0.0000", "nan%"],
        "explore": ["| 0.123 ", "| -0.000 ", "| nan ", "| +0.876 "],
        "exploration_metrics": ["| 0.334 ", "| -0.000 ", "| n/a "],
        "generator": ["**+-0.0**", "**-30.0**", "| — |"],
    }.items():
        missing = [m for m in marks if m not in texts[name]]
        assert not missing, f"{name}: {missing}"
