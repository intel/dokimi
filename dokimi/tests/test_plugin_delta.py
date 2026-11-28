# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for paired plugin/no-plugin delta artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser, main
from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs
from dokimi.report.plugin_delta_report import render_plugin_delta_report
from tests.cli_refusal import refusal


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


@pytest.mark.parametrize("missing", ["baseline", "plugin"])
def test_compare_plugin_runs_names_the_artifact_without_evaluations(missing):
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    artifacts = {"baseline": baseline, "plugin": plugin}
    del artifacts[missing]["evaluations"]
    with_scores = "plugin" if missing == "baseline" else "baseline"

    with pytest.raises(PluginDeltaError, match=f"{missing} artifact has no evaluations to pair with {with_scores}"):
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
    for artifact in (baseline, plugin):
        artifact.update(judge_prompt_sha256="a" * 64, judge_temperature=None,
                        judge_resolved_models=["judge-2026-11-01"])
        artifact["answers"][0]["arms"]["skill"]["metadata"] = {"profile_prompt_sha256": "sha256:matching"}
        for section in ("answers", "evaluations"):
            for row in artifact[section]:
                row["question_text"] = f"How do I finish {row['question_id']}?"
    if stamp is not None:
        baseline["judge_context"] = plugin["judge_context"] = stamp
    for ev, bump in zip(plugin["evaluations"], (5, 15), strict=True):  # the arms must average differently
        ev["scores"]["skill"]["aggregate"] += bump

    out = compare_plugin_runs(baseline, plugin)
    assert (out["plugin_set"], out["total_questions"], out["warnings"]) == ("caveman:full", 2, [])
    assert out["score_deltas"]["skill"] == {"n": 2, "baseline_avg_aggregate": 85.0,
                                            "plugin_avg_aggregate": 95.0, "aggregate_delta": 10.0}


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
    candidate = _arms_artifact("caveman:full")
    candidate["evaluations"][0]["scores"] = {"skill": {"aggregate": 87}}
    candidate["evaluations"][1]["scores"] = {}
    plugin.write_text(json.dumps(candidate), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args([
        "arms",
        "plugin-delta",
        "--baseline",
        str(baseline),
        "--plugin-json",
        str(plugin),
        "--out",
        str(out_json),
    ])
    args.func(args)

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "plugin_delta.v1"
    assert payload["plugin_set"] == "caveman:full"
    assert payload["score_deltas"]["skill"] == {
        "n": 1, "baseline_avg_aggregate": 80.0, "plugin_avg_aggregate": 87.0, "aggregate_delta": 7.0,
    }
    assert payload["score_deltas"]["baseline"]["n"] == 0
    assert payload["score_deltas"]["baseline"]["aggregate_delta"] is None
    md = out_json.with_suffix(".md").read_text(encoding="utf-8")
    assert "| `skill` | 80.0 | 87.0 | +7.0 | 1 |" in md
    assert "| `baseline` | - | - | - | 0 |" in md
    assert "question text was not recorded" in md
    assert any("judge_prompt_sha256" in warning for warning in payload["warnings"])


@pytest.mark.parametrize("section", ["answers", "evaluations"])
def test_compare_plugin_runs_refuses_changed_question_text(section):
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline[section][1]["question_text"] = "How do I start a task?"
    plugin[section][1]["question_text"] = "How do I cancel a task?"

    with pytest.raises(PluginDeltaError, match=rf"different {section} question text for 'q2'"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_warns_when_only_some_questions_lack_text():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    for artifact in (baseline, plugin):
        artifact["answers"][0]["question_text"] = "How do I finish q1?"

    payload = compare_plugin_runs(baseline, plugin)

    assert "answers question text was not recorded for every pair" in payload["warnings"]


def test_compare_plugin_runs_warns_about_null_judge_prompt_digests():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    for artifact in (baseline, plugin):
        artifact.update(judge_prompt_sha256=None, judge_temperature=0.0,
                        judge_resolved_models=["judge-2026-11-01"])

    payload = compare_plugin_runs(baseline, plugin)

    assert "judge settings were not fully recorded (judge_prompt_sha256)" in payload["warnings"]


def test_compare_plugin_runs_refuses_changed_judge_reference_for_the_same_question():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline["answers"][0]["ground_truth_chunk"] = "Multiply the count by eight."
    plugin["answers"][0]["ground_truth_chunk"] = "Multiply the count by nine."

    with pytest.raises(PluginDeltaError, match="different judge references for 'q1'"):
        compare_plugin_runs(baseline, plugin)


@pytest.mark.parametrize("combined", [False, True])
def test_compare_plugin_runs_refuses_an_edited_profile_under_the_same_arm_name(combined):
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    for artifact, digest in ((baseline, "sha256:before"), (plugin, "sha256:after")):
        profile = {"profile_prompt_sha256": digest}
        artifact["answers"][0]["arms"]["skill"]["metadata"] = (
            {"treatments": [{"kind": "docs"}, profile]} if combined else profile
        )

    with pytest.raises(PluginDeltaError, match="different profile prompt digests for arm 'skill'"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_profile_digests_swapped_between_questions():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    for artifact, digests in ((baseline, ("a", "b")), (plugin, ("b", "a"))):
        for answer, digest in zip(artifact["answers"], digests, strict=True):
            answer["arms"]["skill"]["metadata"] = {"profile_prompt_sha256": digest}

    with pytest.raises(PluginDeltaError, match="different profile prompt digests for arm 'skill'"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_edited_profile_digests_on_different_questions():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline["answers"][0]["arms"]["skill"]["metadata"] = {"profile_prompt_sha256": "sha256:before"}
    plugin["answers"][1]["arms"]["skill"]["metadata"] = {"profile_prompt_sha256": "sha256:after"}

    with pytest.raises(PluginDeltaError, match=r"different profile prompt digests for arm 'skill'$"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_warns_about_a_setting_one_run_omits():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    for artifact in (baseline, plugin):
        artifact.update(judge_prompt_sha256="a" * 64, judge_resolved_models=["judge-2026-11-01"])
    plugin["judge_temperature"] = None

    payload = compare_plugin_runs(baseline, plugin)

    assert "judge settings were not fully recorded (judge_temperature)" in payload["warnings"]


def _run_config(**changes) -> dict:
    config = {"max_tokens": 1024, "max_iterations": 8, "grounded_from_topics": False,
              "temperature": None, "seed": None}
    return {**config, **changes}


@pytest.mark.parametrize(("field", "after"), [
    ("max_tokens", 64), ("max_iterations", 1), ("temperature", 1.0), ("grounded_from_topics", True),
])
def test_compare_plugin_runs_refuses_a_changed_answer_budget_or_sampling(field, after):
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline["run_config"], plugin["run_config"] = _run_config(), _run_config(**{field: after})

    with pytest.raises(PluginDeltaError, match=f"different {field}: "):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_refuses_a_seeded_run_against_an_unseeded_one():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline["run_config"], plugin["run_config"] = _run_config(), _run_config(seed=7)

    with pytest.raises(PluginDeltaError, match="seeded and an unseeded run: seed None vs 7"):
        compare_plugin_runs(baseline, plugin)


def test_compare_plugin_runs_pairs_runs_whose_seeds_differ_only_in_value():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline["run_config"], plugin["run_config"] = _run_config(seed=1), _run_config(seed=2)

    payload = compare_plugin_runs(baseline, plugin)

    assert payload["score_deltas"]["skill"]["aggregate_delta"] == 0.0
    assert not any("run_config" in warning for warning in payload["warnings"])


def test_compare_plugin_runs_warns_when_only_one_run_records_run_config():
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    plugin["run_config"] = _run_config(max_tokens=64)

    payload = compare_plugin_runs(baseline, plugin)

    assert ("run_config was recorded by only one run; budgets and sampling were not compared"
            in payload["warnings"])


@pytest.mark.parametrize(("field", "before", "after"), [
    ("judge_prompt_sha256", "a" * 64, "b" * 64),
    ("judge_prompt_sha256", "a" * 64, None),
    ("judge_temperature", 0.0, 0.5),
    ("judge_resolved_models", ["judge-2026-09-01"], ["judge-2026-11-01"]),
])
def test_arms_plugin_delta_cli_refuses_changed_judge_settings(tmp_path, monkeypatch, field, before, after):
    baseline, plugin = _arms_artifact(), _arms_artifact("caveman:full")
    baseline[field], plugin[field] = before, after
    baseline_path, plugin_path = tmp_path / "before.json", tmp_path / "after.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    plugin_path.write_text(json.dumps(plugin), encoding="utf-8")
    out = tmp_path / "delta.json"
    monkeypatch.setattr(sys, "argv", [
        "dokimi", "arms", "plugin-delta", "--baseline", str(baseline_path),
        "--plugin-json", str(plugin_path), "--out", str(out),
    ])

    with refusal() as result:
        main()

    assert result.code == 2
    assert f"different {field}" in result.err
    assert not out.exists()
    assert not out.with_suffix(".md").exists()
