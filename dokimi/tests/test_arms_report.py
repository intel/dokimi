# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the arms-comparison Markdown report."""

from dokimi.eval.arm_summary import TOOL_COUNTS, TOOL_COUNTS_MIXED
from dokimi.report.arms_report import render_arms_report, _md_cell


def test_md_cell_escapes_pipes_and_newlines():
    assert _md_cell("a | b\nc") == r"a \| b c"
    assert _md_cell(None) == ""


def test_report_escapes_question_text_in_table():
    data = {
        "library_name": "oneTBB",
        "model": "m", "provider": "openai",
        "arms": ["baseline"],
        "baseline_arm": "baseline",
        "total_questions": 1,
        "answers": [],
        "evaluations": [
            {"question_id": "q1", "question_text": "What | how\nnewline?",
             "scores": {"baseline": {"aggregate": 70}}}
        ],
        "summary": {"per_arm": {"baseline": {"avg_aggregate": 70.0, "n": 1}}},
    }
    md = render_arms_report(data)
    # The raw pipe inside the question must be escaped so the row stays valid.
    assert r"What \| how" in md
    assert "What | how\nnewline" not in md


def test_report_renders_cost_and_latency_section():
    data = {
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "matrix_cell": "agent-caveman",
        "arms": ["baseline", "skill"], "baseline_arm": "baseline",
        "total_questions": 1, "answers": [],
        "cost_summary": {
            "baseline": {"n": 2, "total_cost_usd": 0.0042, "cost_known_n": 1,
                         "prompt_tokens": 100, "completion_tokens": 40,
                         "total_tokens": 140, "mean_latency_sec": 1.5,
                         "mean_ttft_sec": None, "cache_hit_ratio": 0.5},
            "skill": {"n": 1, "total_cost_usd": None, "cost_known_n": 0,
                      "prompt_tokens": 200, "completion_tokens": 60,
                      "total_tokens": 260, "mean_latency_sec": 2.0,
                      "mean_ttft_sec": 0.3, "cache_hit_ratio": None},
        },
    }
    md = render_arms_report(data)
    assert "- Matrix cell: `agent-caveman`" in md
    assert "## Cost & latency" in md
    assert "$0.0042 (1/2)" in md  # partial-coverage flag: 1 of 2 rows priced
    assert "50.0%" in md          # cache-hit ratio rendered as percent
    assert "0.300" in md          # ttft for skill arm


def test_report_renders_paired_significance_section():
    data = {
        "library_name": "dpnp", "model": "m", "provider": "bedrock",
        "arms": ["baseline", "skill:demo-skill"],
        "baseline_arm": "baseline",
        "total_questions": 6, "answers": [],
        "summary": {
            "per_arm": {
                "baseline": {"avg_aggregate": 50.0, "n": 6},
                "skill:demo-skill": {
                    "avg_aggregate": 62.0, "n": 6, "delta_vs_baseline": 12.0,
                    "significance": {
                        "n": 6, "delta_mean": 12.0, "cohens_d": 1.35,
                        "effect": "large", "ci_lo": 9.5, "ci_hi": 14.5,
                        "p_ttest": 0.00002, "p_adjusted": 0.00006,
                        "significant": True, "significant_adjusted": True,
                        "correction": "holm", "family_size": 1,
                    },
                },
            }
        },
    }
    md = render_arms_report(data)
    assert "## Paired significance vs baseline" in md
    assert "Holm-Bonferroni" in md
    assert "<0.0001" in md          # tiny p-values are not shown as 0.0000
    assert "[+9.5, +14.5]" in md    # bootstrap CI on the paired delta
    assert "| `skill:demo-skill` | +12.0 |" in md


def test_report_renders_interaction_section():
    data = {
        "library_name": "dpnp", "model": "m", "provider": "bedrock",
        "arms": ["baseline", "docs", "skill:x", "docs+skill:x"],
        "baseline_arm": "baseline",
        "total_questions": 6, "answers": [],
        "summary": {
            "per_arm": {"baseline": {"avg_aggregate": 50.0, "n": 6}},
            "interactions": [{
                "arm_a": "docs", "arm_b": "skill:x",
                "combined_arm": "docs+skill:x", "baseline_arm": "baseline",
                "delta_a": 2.0, "delta_b": 12.0, "delta_combined": 6.0,
                "additive_expectation": 14.0, "interaction": -8.0,
                "ci_lo": -10.0, "ci_hi": -6.0, "p_ttest": 0.001, "n": 6,
            }],
        },
    }
    md = render_arms_report(data)
    assert "## Factorial interaction" in md
    assert "**-8.0**" in md          # the interaction term is emphasised
    assert "+14.0" in md             # additive expectation shown for contrast
    assert "[-10.0, -6.0]" in md


def _sig_data(**significance):
    """One-arm artifact whose significance block is fully caller-controlled."""
    block = {
        "n": 6, "delta_mean": 5.0, "cohens_d": 0.8, "effect": "large",
        "ci_lo": 1.0, "ci_hi": 9.0, "correction": "holm", "family_size": 1,
    }
    block.update(significance)
    return {
        "library_name": "dpnp", "model": "m", "provider": "bedrock",
        "arms": ["baseline", "docs"], "baseline_arm": "baseline",
        "total_questions": 6, "answers": [],
        "summary": {
            "per_arm": {
                "baseline": {"avg_aggregate": 50.0, "n": 6},
                "docs": {"avg_aggregate": 55.0, "n": 6, "significance": block},
            }
        },
    }


def test_sig_column_follows_the_adjusted_verdict_not_the_raw_one():
    """The column header promises correction, so it must not read the raw p.

    A borderline arm whose raw p clears alpha and whose adjusted p does not is
    exactly the false positive the correction exists to stop; the two fields
    must be able to disagree in a test or nothing pins which one is rendered.
    """
    md = render_arms_report(
        _sig_data(
            p_ttest=0.0339, p_adjusted=0.0678,
            significant=True, significant_adjusted=False,
        )
    )
    row = next(line for line in md.splitlines() if line.startswith("| `docs` | +"))
    assert row.endswith("| 0.0339 | 0.0678 | no | 6 |")


def test_sig_column_shows_a_dash_when_no_test_could_run():
    """None is "unknown", not "no": absent evidence is not evidence of absence."""
    md = render_arms_report(
        _sig_data(
            cohens_d=None, effect="unknown", p_ttest=None, p_adjusted=None,
            significant=None, significant_adjusted=None,
        )
    )
    row = next(line for line in md.splitlines() if line.startswith("| `docs` | +"))
    # Cohen's d_z, both p columns and the verdict are all dashes — and the row
    # renders at all, rather than raising on a None format spec.
    assert row.endswith("| — | unknown | — | — | — | 6 |")


def test_interaction_row_reports_its_own_adjusted_verdict():
    data = {
        "library_name": "dpnp", "model": "m", "provider": "bedrock",
        "arms": ["baseline", "docs", "skill:x", "docs+skill:x"],
        "baseline_arm": "baseline",
        "total_questions": 6, "answers": [],
        "summary": {
            "per_arm": {"baseline": {"avg_aggregate": 50.0, "n": 6}},
            "interactions": [{
                "arm_a": "docs", "arm_b": "skill:x",
                "combined_arm": "docs+skill:x", "baseline_arm": "baseline",
                "delta_a": 2.0, "delta_b": 12.0, "delta_combined": 6.0,
                "additive_expectation": 14.0, "interaction": -8.0,
                "ci_lo": -10.0, "ci_hi": -6.0, "p_ttest": 0.03,
                "p_adjusted": 0.06, "significant": True,
                "significant_adjusted": False, "correction": "holm",
                "family_size": 2, "n": 6,
            }],
        },
    }
    md = render_arms_report(data)
    row = next(
        line for line in md.splitlines() if line.startswith("| `docs` | `skill:x` |")
    )
    assert row.endswith("| 0.0300 | 0.0600 | no | 6 |")
    assert "interaction contrast(s)" in md


def test_report_omits_stats_sections_when_absent():
    data = {
        "library_name": "dpnp", "model": "m", "provider": "bedrock",
        "arms": ["baseline"], "baseline_arm": "baseline",
        "total_questions": 1, "answers": [],
        "summary": {"per_arm": {"baseline": {"avg_aggregate": 50.0, "n": 1}}},
    }
    md = render_arms_report(data)
    assert "Paired significance" not in md
    assert "Factorial interaction" not in md


def test_report_renders_agentic_section():
    data = {
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "arms": ["agent"], "baseline_arm": "baseline", "total_questions": 1,
        "answers": [
            {"question_id": "q1", "arms": {"agent": {"agentic": True, "tool_call_count": 2, "iterations": 3}}}
        ],
    }
    md = render_arms_report(data)
    assert "Agentic tool use" in md
    assert "`agent`" in md
    assert "Tool-use rate" in md


def test_report_renders_skill_load_rate_from_summary():
    data = {
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "arms": ["skill_agent:demo-skill"],
        "baseline_arm": "baseline",
        "total_questions": 2,
        "answers": [],
        "agentic_usage_summary": {
            "skill_agent:demo-skill": {
                "n": 2,
                "avg_tool_calls": 0.5,
                "tool_use_rate": 0.5,
                "avg_iterations": 1.5,
                "skill_load_rate": 0.5,
            }
        },
    }
    md = render_arms_report(data)
    assert "Skill-load rate" in md
    assert "| `skill_agent:demo-skill` | 50% | 0.5 | 50% | 1.5 | 2 |" in md


def _agentic_report(stats):
    return render_arms_report({
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "arms": list(stats), "baseline_arm": "baseline", "total_questions": 1,
        "answers": [], "agentic_usage_summary": stats,
    })


def test_report_flags_tool_counts_that_include_failed_calls():
    row = {"n": 1, "avg_tool_calls": 1.0, "tool_use_rate": 1.0, "avg_iterations": 2.0}
    md = _agentic_report({
        "new": {**row, "tool_counts": TOOL_COUNTS},
        "old": row,
        "mixed": {**row, "tool_counts": TOOL_COUNTS_MIXED},
    })
    assert "Tool counts of `old`, `mixed` include failed calls" in md
    assert "Tool counts of `new`" not in md
    assert "include failed calls" not in _agentic_report({"new": {**row, "tool_counts": TOOL_COUNTS}})


def test_summary_table_shows_the_errors_count_beside_n():
    data = {
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "arms": ["baseline", "skill"], "baseline_arm": "baseline", "total_questions": 3,
        "summary": {"per_arm": {
            "baseline": {"avg_aggregate": 70.0, "n": 2, "errors": 1},
            "skill": {"avg_aggregate": 60.0, "n": 1, "errors": 2, "delta_vs_baseline": None},
        }},
    }
    md = render_arms_report(data)
    assert "| Arm | Avg | Δ vs baseline | n | Errors |" in md
    assert "| `baseline` | 70.0 | (baseline) | 2 | 1 |" in md
    assert "| `skill` | 60.0 | — | 1 | 2 |" in md


def test_summary_table_reads_an_absent_errors_count_as_unknown_not_zero():
    """An artifact written before the count has no `errors` key; a real zero is still a zero."""
    data = {
        "library_name": "oneTBB", "model": "m", "provider": "openai",
        "arms": ["baseline", "old", "clean"], "baseline_arm": "baseline", "total_questions": 3,
        "summary": {"per_arm": {
            "baseline": {"avg_aggregate": 70.0, "n": 2, "errors": 0},
            "old": {"avg_aggregate": 60.0, "n": 1, "delta_vs_baseline": -10.0},
            "clean": {"avg_aggregate": 65.0, "n": 2, "errors": 0, "delta_vs_baseline": -5.0},
        }},
    }
    md = render_arms_report(data)
    assert "| `old` | 60.0 | -10.0 | 1 | — |" in md
    assert "| `clean` | 65.0 | -5.0 | 2 | 0 |" in md
