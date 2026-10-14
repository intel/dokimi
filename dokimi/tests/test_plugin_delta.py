# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for paired plugin/no-plugin delta artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs
from dokimi.report.plugin_delta_report import render_plugin_delta_report


def _arms_artifact(plugin_set: str = "none") -> dict:
    plugin_id = "empty" if plugin_set == "none" else "sha"
    return {
        "schema_version": "arms.v1",
        "library_name": "oneTBB",
        "model": "m",
        "provider": "openai",
        "harness": "agent",
        "judge_model": "judge-m",
        "judge_provider": "openai",
        "plugin_set": plugin_set,
        "plugin_set_id": f"sha256:{plugin_id}",
        "plugins": [] if plugin_set == "none" else [{"id": "caveman"}],
        "arms": ["baseline", "skill"],
        "baseline_arm": "baseline",
        "total_questions": 2,
        "answers": [
            {
                "question_id": "q1",
                "arms": {
                    "baseline": {
                        "metrics": {
                            "raw_answer_chars": 100,
                            "final_answer_chars": 100,
                        }
                    },
                    "skill": {
                        "metrics": {
                            "raw_answer_chars": 140,
                            "final_answer_chars": 140,
                        }
                    },
                },
            },
            {
                "question_id": "q2",
                "arms": {
                    "baseline": {
                        "metrics": {
                            "raw_answer_chars": 80,
                            "final_answer_chars": 80,
                        }
                    },
                    "skill": {
                        "metrics": {
                            "raw_answer_chars": 120,
                            "final_answer_chars": 120,
                        }
                    },
                },
            },
        ],
        "evaluations": [
            {
                "question_id": "q1",
                "scores": {
                    "baseline": {"aggregate": 60},
                    "skill": {"aggregate": 80},
                },
            },
            {
                "question_id": "q2",
                "scores": {
                    "baseline": {"aggregate": 70},
                    "skill": {"aggregate": 90},
                },
            },
        ],
        "cost_summary": {
            "baseline": {
                "total_cost_usd": 0.02,
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "mean_latency_sec": 1.0,
                "mean_ttft_sec": None,
            },
            "skill": {
                "total_cost_usd": 0.03,
                "prompt_tokens": 150,
                "completion_tokens": 60,
                "total_tokens": 210,
                "mean_latency_sec": 1.5,
                "mean_ttft_sec": 0.2,
            },
        },
    }


def test_compare_plugin_runs_computes_paired_deltas():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    # Simulate a brevity plugin: lower scores, fewer completion tokens/chars.
    plugin["evaluations"][0]["scores"]["skill"]["aggregate"] = 76
    plugin["evaluations"][1]["scores"]["skill"]["aggregate"] = 86
    plugin["cost_summary"]["skill"]["completion_tokens"] = 30
    plugin["answers"][0]["arms"]["skill"]["metrics"]["final_answer_chars"] = 70
    plugin["answers"][1]["arms"]["skill"]["metrics"]["final_answer_chars"] = 60

    out = compare_plugin_runs(baseline, plugin)

    validate_artifact("plugin_delta", out)
    assert out["plugin_set"] == "caveman:full"
    assert out["score_deltas"]["skill"]["aggregate_delta"] == -4.0
    assert out["cost_deltas"]["skill"]["completion_tokens"]["delta"] == -30
    assert out["answer_text_deltas"]["skill"]["final_answer_chars"]["delta"] == -65


def test_compare_plugin_runs_refuses_cross_harness():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    plugin["harness"] = "single-shot"

    with pytest.raises(PluginDeltaError, match="different harness"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_mismatched_question_ids():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    plugin["answers"][1]["question_id"] = "different-q"

    with pytest.raises(PluginDeltaError, match="answers question IDs/order"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_mismatched_evaluation_question_ids():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    plugin["evaluations"][1]["question_id"] = "different-q"

    with pytest.raises(PluginDeltaError, match="evaluations question IDs/order"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_mismatched_judge_identity():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    plugin["judge_model"] = "other-judge"

    with pytest.raises(PluginDeltaError, match="different judge_model"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_requires_judge_identity_for_scored_artifacts():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    del baseline["judge_provider"]

    with pytest.raises(PluginDeltaError, match="without stamped judge_provider"):
        compare_plugin_runs(baseline, plugin)


_V1 = "treatment_material.v1"


@pytest.mark.parametrize(
    "control_stamp, plugin_stamp",
    [(None, _V1), (_V1, None), (_V1, "treatment_material.v2")],
)
def test_compare_plugin_runs_refuses_judge_context_that_differs(control_stamp, plugin_stamp):
    """A pre-#51 run was judged on 200-char snippets; its delta against a run judged
    on the full material would mix two judge protocols."""
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    for art, stamp in ((baseline, control_stamp), (plugin, plugin_stamp)):
        if stamp is not None:
            art["judge_context"] = stamp

    with pytest.raises(PluginDeltaError, match="different judge_context"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_a_mixed_run_even_against_another_mixed_one():
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    baseline["judge_context"] = plugin["judge_context"] = _V1 + "+snippet_fallback"
    with pytest.raises(PluginDeltaError, match="mix stored"):
        compare_plugin_runs(baseline, plugin)


@pytest.mark.parametrize("stamp", [None, _V1])
def test_compare_plugin_runs_allows_judge_context_that_matches(stamp):
    baseline = _arms_artifact()
    plugin = _arms_artifact("caveman:full")
    if stamp is not None:
        baseline["judge_context"] = plugin["judge_context"] = stamp

    compare_plugin_runs(baseline, plugin)


def test_plugin_delta_report_renders_key_sections():
    out = compare_plugin_runs(_arms_artifact(), _arms_artifact("caveman:full"))
    md = render_plugin_delta_report(out)

    assert "# Plugin delta" in md
    assert "Judge score deltas" in md
    assert "Cost and token deltas" in md
    assert "Answer length deltas" in md


def test_arms_plugin_delta_cli_writes_json_and_markdown(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    plugin = tmp_path / "plugin.json"
    out_json = tmp_path / "delta.json"
    baseline.write_text(json.dumps(_arms_artifact()), encoding="utf-8")
    plugin.write_text(json.dumps(_arms_artifact("caveman:full")), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args([
        "arms",
        "plugin-delta",
        "--baseline-json",
        str(baseline),
        "--plugin-json",
        str(plugin),
        "--out-json",
        str(out_json),
    ])
    args.func(args)

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "plugin_delta.v1"
    assert payload["plugin_set"] == "caveman:full"
    assert out_json.with_suffix(".md").is_file()
