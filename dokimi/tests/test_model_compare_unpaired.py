# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report model-compare`` limitation text when no question is scored in both arms (#965)."""

from __future__ import annotations

from dokimi.report.model_compare import generate_section_report


_UNPAIRED_ARM = "docs+skill:onetbb-quickstart"


def _unpaired_run(model):
    """#965: baseline scored only on q-loop, treatment only on q0001."""
    items = [("q-loop", 50, None), ("q0001", None, 90)]
    return {
        "schema_version": "arms.v1", "model": model, "arms": ["baseline", _UNPAIRED_ARM],
        "baseline_arm": "baseline",
        "answers": [{"question_id": q, "question_text": q, "arms": {"baseline": {"answer": "c"}, _UNPAIRED_ARM: {"answer": "t"}}}
                    for q, _, _ in items],
        "evaluations": [{"question_id": q, "question_text": q, "scores": {"baseline": {"aggregate": b}, _UNPAIRED_ARM: {"aggregate": a}}}
                        for q, b, a in items],
    }


def test_two_arm_runs_without_a_paired_score_name_both_arms_not_baseline_only():
    """#965: arms present but never scored on one question must not read as baseline-only."""
    runs = [("haiku", _unpaired_run("haiku")), ("opus", _unpaired_run("opus"))]
    text = "\n".join(generate_section_report(runs, ["haiku", "opus"], "regular"))
    assert "0 (2 unscored)" in text
    assert "contain only **baseline arm**" not in text
    assert f"`baseline`, `{_UNPAIRED_ARM}`, but no question has scores in both arms" in text


def test_legacy_runs_without_a_paired_score_name_both_arms():
    """#965: the legacy format drops unpaired rows, so the arms come from the artifact."""
    run = _unpaired_run("m")
    del run["evaluations"]
    for ans, (base, treated) in zip(run["answers"], ((50, None), (None, 90)), strict=True):
        ans["arms"]["baseline"]["evaluation"] = {"aggregate_score": base}
        ans["arms"][_UNPAIRED_ARM]["evaluation"] = {"aggregate_score": treated}
    text = "\n".join(generate_section_report([("a", run)], ["a"], "regular"))
    assert "contain only **baseline arm**" not in text
    assert f"`{_UNPAIRED_ARM}`" in text


def test_baseline_only_run_keeps_the_baseline_only_limitation():
    """#965: a run with no treatment arm still gets the baseline-only text."""
    run = _unpaired_run("m")
    run["arms"] = ["baseline"]
    for ans in run["answers"]:
        del ans["arms"][_UNPAIRED_ARM]
    for ev in run["evaluations"]:
        del ev["scores"][_UNPAIRED_ARM]
    text = "\n".join(generate_section_report([("a", run)], ["a"], "regular"))
    assert "contain only **baseline arm**" in text


def _paired_run(model, qid):
    """#965: one question scored in both arms."""
    return {"schema_version": "arms.v1", "model": model, "baseline_arm": "baseline", "arms": ["baseline", "docs"],
            "answers": [{"question_id": qid, "question_text": qid, "arms": {"baseline": {"answer": "c"}, "docs": {"answer": "t"}}}],
            "evaluations": [{"question_id": qid, "question_text": qid,
                             "scores": {"baseline": {"aggregate": 50}, "docs": {"aggregate": 90}}}]}


def test_paired_scores_on_disjoint_questions_are_not_called_unscored_in_both_arms():
    """#965: each run has a paired score, only no question is shared between the runs."""
    runs = [("a", _paired_run("m1", "q1")), ("b", _paired_run("m2", "q2"))]
    text = "\n".join(generate_section_report(runs, ["a", "b"], "regular"))
    assert "no question has scores in both arms" not in text
    assert "contain only **baseline arm**" not in text
    assert "once replicates are pooled" in text


def _two_scored(model, shared_treated, paired_id):
    """#965: a shared question without a treatment score, plus a question paired in this run only."""
    def ev(qid, treated):
        return {"question_id": qid, "scores": {"baseline": {"aggregate": 50}, "docs": {"aggregate": treated}}}
    return {"schema_version": "arms.v1", "model": model, "baseline_arm": "baseline", "arms": ["baseline", "docs"],
            "evaluations": [ev("shared", shared_treated), ev(paired_id, 90)]}


def test_pooling_does_not_hide_a_paired_score_from_the_limitation():
    """#965: replicate a has a 50/90 pair that pooling drops; the text must not deny any pair."""
    a = _paired_run("m", "q")
    b = _paired_run("m", "q")
    b["evaluations"][0]["scores"]["docs"]["aggregate"] = None
    text = "\n".join(generate_section_report([("a", a), ("b", b)], ["a", "b"], "regular", pool_replicates=True))
    assert "no question has scores in both arms" not in text
    assert "contain only **baseline arm**" not in text
    assert "once replicates are pooled" in text


def test_shared_but_unpaired_question_is_not_told_to_share_a_question():
    """#965: the runs share a question already; what is missing is a shared paired score."""
    runs = [("a", _two_scored("m1", None, "q1")), ("b", _two_scored("m2", None, "q2"))]
    text = "\n".join(generate_section_report(runs, ["a", "b"], "regular"))
    assert "Common questions evaluated: **1**" in text
    assert "until the runs share a question" not in text
    assert "contain only **baseline arm**" not in text
    assert "once replicates are pooled" in text


def test_pooled_legacy_runs_keep_the_treatment_of_the_dropped_replicate():
    """#965: pooling keeps the first replicate's artifact; the docs arm of the second must still be named."""
    a = _paired_run("m", "q")
    del a["arms"], a["evaluations"]
    a["answers"][0]["arms"].pop("docs")
    b = _paired_run("m", "q")
    del b["arms"], b["evaluations"]
    for arm, score in (("baseline", 50), ("docs", 90)):
        b["answers"][0]["arms"][arm]["evaluation"] = {"aggregate_score": score}
    text = "\n".join(generate_section_report([("a", a), ("b", b)], ["a", "b"], "regular", pool_replicates=True))
    assert "contain only **baseline arm**" not in text
    assert "`docs`" in text


def test_selected_treatment_limitation_names_only_that_comparison():
    """#965: baseline/docs is unpaired while baseline/skill is paired; only docs is named."""
    run = _paired_run("m", "q")
    run["arms"] = ["baseline", "docs", "skill:demo"]
    run["evaluations"][0]["scores"] = {"baseline": {"aggregate": 50}, "docs": None, "skill:demo": {"aggregate": 90}}
    run["answers"][0]["arms"]["skill:demo"] = {"answer": "s"}
    text = "\n".join(generate_section_report([("a", run)], ["a"], "regular", treatment_arm="docs"))
    assert "`baseline`, `docs`, but no question has scores in both arms" in text
    assert "skill:demo" not in text.split("Report Limitation")[1].split("\n\n")[0]
