# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Replicate handling: pooling, duplicate detection, and report provenance.

A "replicate" is a rerun of an identical configuration — same answer model,
same provider, same judge. Sampling is non-deterministic, so replicates differ;
treating them as rival models reports that noise as a model ranking.
"""

import pytest

from dokimi.utils import InputError
from dokimi.report.model_compare import (
    check_run_consistency,
    group_replicates,
    pool_replicate_scores,
    replicate_key,
)


def _run(model, provider="amazon-bedrock", judge="judgeA", baseline="baseline"):
    return {
        "model": model,
        "provider": provider,
        "judge_model": judge,
        "judge_provider": provider,
        "baseline_arm": baseline,
        "arms": ["baseline", "skill_agent:s"],
    }


# ── grouping ────────────────────────────────────────────────────────────────
def test_group_replicates_groups_identical_configs():
    runs = [("r1", _run("m1")), ("r2", _run("m1")), ("r3", _run("m2"))]
    groups = group_replicates(runs)
    assert sorted(groups.values()) == [["r1", "r2"], ["r3"]]
    assert replicate_key(runs[0][1])[:4] == ("m1", "amazon-bedrock", "judgeA", "amazon-bedrock")


def test_group_replicates_separates_on_sampling_but_pools_across_seeds():
    def run(**cfg):
        return {**_run("m1"), "run_config": {"temperature": 0.0, "max_tokens": 4096, **cfg}}
    hot = [("r1", run(seed=1)), ("r2", run(seed=2)), ("r3", run(seed=1, temperature=0.7)),
           ("r4", run(seed=None)), ("r5", run(seed=1, max_tokens=512))]
    assert sorted(group_replicates(hot).values()) == [["r1", "r2"], ["r3"], ["r4"], ["r5"]]
    judged = [("a", {**_run("m1"), "judge_prompt_sha256": "sha256:a"}),
              ("b", {**_run("m1"), "judge_prompt_sha256": "sha256:b"}),
              ("c", {**_run("m1"), "judge_prompt_sha256": "sha256:a", "judge_temperature": 0.0})]
    assert len(group_replicates(judged)) == 3


def test_group_replicates_separates_on_judge():
    # Same answer model judged by two different judges is NOT a replicate pair.
    runs = [("r1", _run("m1", judge="judgeA")), ("r2", _run("m1", judge="judgeB"))]
    groups = group_replicates(runs)
    assert len(groups) == 2


# ── duplicate detection ─────────────────────────────────────────────────────
def test_duplicate_configs_are_a_hard_error_when_not_pooling():
    runs = [("r1", _run("m1")), ("r2", _run("m1")), ("r3", _run("m2"))]
    with pytest.raises(InputError) as exc:
        check_run_consistency(runs, "golden")
    msg = str(exc.value)
    assert "--pool-replicates" in msg
    assert "m1" in msg


def test_duplicate_configs_accepted_when_pooling():
    runs = [("r1", _run("m1")), ("r2", _run("m1"))]
    warnings = check_run_consistency(runs, "golden", pool_replicates=True)
    assert not any("SystemExit" in w for w in warnings)


def test_distinct_models_still_pass_without_pooling():
    runs = [("r1", _run("m1")), ("r2", _run("m2"))]
    assert check_run_consistency(runs, "golden") == []


def test_all_same_model_is_an_error_not_just_a_warning():
    # Previously this only produced a soft stderr warning while the report
    # ranked a model against itself.
    runs = [("r1", _run("m1")), ("r2", _run("m1"))]
    with pytest.raises(InputError):
        check_run_consistency(runs, "golden")


# ── pooling maths ───────────────────────────────────────────────────────────
def test_pool_replicate_scores_averages_per_question_across_replicates():
    scores = {
        "r1": [{"question_id": "q1", "with_docs": 70.0, "without_docs": 40.0, "delta": 30.0}],
        "r2": [{"question_id": "q1", "with_docs": 80.0, "without_docs": 50.0, "delta": 30.0}],
    }
    pooled = pool_replicate_scores(scores, ["r1", "r2"])
    assert len(pooled) == 1
    row = pooled[0]
    assert row["question_id"] == "q1"
    assert row["with_docs"] == pytest.approx(75.0)
    assert row["without_docs"] == pytest.approx(45.0)
    assert row["delta"] == pytest.approx(30.0)
    assert row["n_replicates"] == 2


def test_pool_replicate_scores_drops_a_question_missing_from_a_replicate():
    scores = {
        "r1": [{"question_id": "q1", "with_docs": 70.0, "without_docs": 40.0, "delta": 30.0},
               {"question_id": "q2", "with_docs": 60.0, "without_docs": 30.0, "delta": 30.0}],
        "r2": [{"question_id": "q1", "with_docs": 80.0, "without_docs": 50.0, "delta": 30.0}],
    }
    pooled = pool_replicate_scores(scores, ["r1", "r2"])
    assert [r["question_id"] for r in pooled] == ["q1"]
    assert pooled[0]["n_replicates"] == 2


def test_pool_replicate_scores_drops_a_question_a_replicate_scored_on_one_arm_only():
    # Averaging each field alone would give with 75, without 40, delta 30.
    scores = {
        "r1": [{"question_id": "q1", "with_docs": 70.0, "without_docs": 40.0, "delta": 30.0}],
        "r2": [{"question_id": "q1", "with_docs": 80.0, "without_docs": None, "delta": None}],
    }
    assert pool_replicate_scores(scores, ["r1", "r2"]) == []


def test_pool_replicate_scores_keeps_an_unpaired_question():
    scores = {"r1": [{"question_id": "q1", "with_docs": 80.0, "without_docs": None, "delta": None}]}
    (q1,) = pool_replicate_scores(scores, ["r1"])
    assert (q1["with_docs"], q1["without_docs"], q1["delta"]) == (80.0, None, None)


def test_pool_replicate_scores_single_replicate_is_identity():
    scores = {"r1": [{"question_id": "q1", "with_docs": 70.0, "without_docs": 40.0, "delta": 30.0}]}
    pooled = pool_replicate_scores(scores, ["r1"])
    assert pooled[0]["with_docs"] == pytest.approx(70.0)
    assert pooled[0]["n_replicates"] == 1


# ── pooled group labels ─────────────────────────────────────────────────────
def test_replicate_label_uses_common_prefix_of_run_ids():
    from dokimi.report.model_compare import replicate_label

    assert replicate_label(["haiku45_run01", "haiku45_run02"]) == "haiku45[n=2]"
    assert replicate_label(["h1", "h2", "h3"]) == "h[n=3]"


def test_replicate_label_single_run_is_the_run_id():
    from dokimi.report.model_compare import replicate_label

    assert replicate_label(["kimi25"]) == "kimi25"


def test_replicate_label_falls_back_when_no_common_prefix():
    from dokimi.report.model_compare import replicate_label

    # No shared prefix: must still produce a usable, non-empty label.
    label = replicate_label(["alpha", "beta"])
    assert label.endswith("[n=2]") and len(label) > len("[n=2]")


# ── panel usable where a single Judge is expected ────────────────────────────
def test_judge_panel_score_answer_matches_the_judge_contract(monkeypatch):
    # ArmRunner.judge() calls judge.score_answer(); a panel lacking it made
    # panels unreachable from the arms track entirely.
    from dokimi.eval import JudgePanel
    import dokimi.eval.panel as panel_mod

    payload = (
        '{"correctness": 80, "completeness": 70, "specificity": 60,'
        ' "code_quality": 90, "actionability": 50, "reasoning": "ok"}'
    )
    monkeypatch.setattr(panel_mod, "llm_call", lambda *a, **k: payload)

    from dokimi.eval.panel import JudgeConfig

    panel = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m", provider="openai"),
        JudgeConfig(role="doc_reviewer", model="m", provider="openai"),
    ])
    out = panel.score_answer(
        library_name="oneTBB", question="q", answer="a", context="c"
    )

    # The field ArmRunner and extract_scores both key on.
    assert isinstance(out["aggregate"], (int, float))
    # Panel-only provenance: the reason to prefer a panel.
    assert out["panel"]["n_judges"] == 2
    assert out["panel"]["n_valid"] == 2
    # Not 0.0 even on identical raw scores: each role applies its own
    # ROLE_WEIGHTS, so the per-role aggregates differ by design.
    assert isinstance(out["panel"]["std_aggregate"], float)
    assert 0.0 <= out["panel"]["agreement_score"] <= 1.0
    assert out["panel"]["disagreement_flag"] is False
    assert [v["role"] for v in out["panel"]["votes"]] == [
        "technical_expert", "doc_reviewer",
    ] or sorted(v["role"] for v in out["panel"]["votes"]) == [
        "doc_reviewer", "technical_expert",
    ]


def test_judge_panel_is_exported_from_eval():
    import dokimi.eval as E

    assert "JudgePanel" in E.__all__


# ── sampling-capability pre-flight ──────────────────────────────────────────
def test_unsupported_sampling_params_flags_seed_on_bedrock():
    # Bedrock accepts temperature but rejects seed; catching that up front turns
    # a wasted run with all-null scores into an immediate error.
    from dokimi.llm import unsupported_sampling_params

    bad = unsupported_sampling_params(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0", "amazon-bedrock",
        temperature=0.0, seed=42,
    )
    assert bad == ["seed"]


def test_unsupported_sampling_params_empty_when_nothing_requested():
    from dokimi.llm import unsupported_sampling_params

    assert unsupported_sampling_params("m", "amazon-bedrock") == []


def test_unsupported_sampling_params_allows_temperature_on_bedrock():
    from dokimi.llm import unsupported_sampling_params

    bad = unsupported_sampling_params(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0", "amazon-bedrock",
        temperature=0.0,
    )
    assert bad == []


def test_judge_panel_exposes_model_and_provider_for_provenance():
    # build_output records judge.model/.provider, and replicate_key keys on
    # them; a panel exposing neither wrote nulls and made distinct panels look
    # like replicates of one configuration.
    from dokimi.eval import JudgePanel
    from dokimi.eval.panel import JudgeConfig

    panel = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m1", provider="amazon-bedrock"),
        JudgeConfig(role="doc_reviewer", model="m1", provider="amazon-bedrock"),
    ])
    assert panel.model == "panel[technical_expert+doc_reviewer]:m1"
    assert panel.provider == "amazon-bedrock"

    other = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m1", provider="amazon-bedrock"),
    ])
    # Distinct panels must not collide, or they would pool as replicates.
    assert other.model != panel.model


def test_judge_panel_marks_mixed_providers():
    from dokimi.eval import JudgePanel
    from dokimi.eval.panel import JudgeConfig

    panel = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m1", provider="openai"),
        JudgeConfig(role="doc_reviewer", model="m2", provider="anthropic"),
    ])
    assert panel.provider.startswith("mixed:")
    assert "m1" in panel.model and "m2" in panel.model


# ── configuration identity beyond the models ────────────────────────────────
def _arms_v1(plugin_set="none", harness="arms-runner", arms=("baseline", "skill:s")):
    plugin_set_id = "sha256:none" if plugin_set == "none" else f"sha256:{plugin_set}"
    return {
        "schema_version": "arms.v1",
        "library_name": "oneTBB",
        "model": "m1",
        "provider": "amazon-bedrock",
        "harness": harness,
        "plugin_set": plugin_set,
        "plugin_set_id": plugin_set_id,
        "plugins": [],
        "cell": f"m1|{harness}|{plugin_set}",
        "arms": list(arms),
        "baseline_arm": "baseline",
        "run_config": {"temperature": 1.0, "seed": None, "max_tokens": None},
        "judge_model": "judgeA",
        "judge_provider": "amazon-bedrock",
        "judge_temperature": None,
        "judge_prompt_sha256": "sha256:p",
        "evaluations": [
            {"question_id": f"q{i}", "scores": {"baseline": {"aggregate": 50.0 + i},
                                                 "skill:s": {"aggregate": 60.0 + 2 * i}}}
            for i in range(4)
        ],
    }


def _model_compare(tmp_path, first, second, *extra):
    import json

    from dokimi.cli import build_parser

    paths = []
    for name, data in (("a", first), ("b", second)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "out.md"
    args = build_parser().parse_args(["report", "model-compare", "--regular-runs", *paths,
                                      "--run-ids", "run1,run2", "--out", str(out), *extra])
    args.func(args)
    return out.read_text(encoding="utf-8")


@pytest.mark.parametrize("other", [
    {"plugin_set": "lint"},
    {"harness": "other-harness"},
    {"arms": ("baseline", "skill:s", "docs")},
])
def test_runs_differing_in_plugin_set_harness_or_arms_are_not_replicates(tmp_path, other):
    with pytest.raises(InputError, match="--pool-replicates"):
        _model_compare(tmp_path, _arms_v1(), _arms_v1())
    assert "pools 2 replicates" in _model_compare(tmp_path, _arms_v1(), _arms_v1(),
                                                  "--pool-replicates")

    _model_compare(tmp_path, _arms_v1(), _arms_v1(**other))
    pooled = _model_compare(tmp_path, _arms_v1(), _arms_v1(**other), "--pool-replicates")
    assert "pools 2 replicates" not in pooled
