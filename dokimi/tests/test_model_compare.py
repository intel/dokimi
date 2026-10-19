# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for dokimi.report.model_compare (package module)."""

from __future__ import annotations

import json

import pytest

from dokimi.report.model_compare import (
    check_run_consistency,
    extract_scores,
    generate_combined_report,
    generate_section_report,
    load_run,
    non_baseline_arms,
    prompt_digests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _eval_run(questions: list[dict], baseline: str = "baseline", **meta) -> dict:
    """Build a minimal arms artifact with the evaluations format."""
    evaluations = []
    for q in questions:
        evaluations.append(
            {
                "question_id": q["id"],
                "question_text": q.get("text", ""),
                "category": q.get("category", ""),
                "difficulty": q.get("difficulty", "easy"),
                "persona": q.get("persona", ""),
                "scores": q.get("scores", {}),
            }
        )
    data: dict = {
        "baseline_arm": baseline,
        "model": meta.get("model", "model-x"),
        "provider": meta.get("provider", "prov"),
        "evaluations": evaluations,
    }
    if "question_set_hash" in meta:
        data["question_set_hash"] = meta["question_set_hash"]
    return data


def _q(qid: str, baseline_score=60, treatment_score=80, arm="with-context", **kw) -> dict:
    return {
        "id": qid,
        "scores": {"baseline": {"aggregate": baseline_score}, arm: {"aggregate": treatment_score}},
        **kw,
    }


# ---------------------------------------------------------------------------
# load_run
# ---------------------------------------------------------------------------


def test_load_run_valid(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({"baseline_arm": "baseline"}), encoding="utf-8")
    data = load_run(str(p))
    assert data["baseline_arm"] == "baseline"


def test_load_run_missing_file():
    with pytest.raises(SystemExit, match="File not found"):
        load_run("/nonexistent/path/run.json")


def test_load_run_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json}", encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid JSON"):
        load_run(str(p))


# ---------------------------------------------------------------------------
# check_run_consistency
# ---------------------------------------------------------------------------


def _run_pair(run_id, **kw):
    return (run_id, {"baseline_arm": kw.get("baseline", "baseline"),
                     "model": kw.get("model", "m"), "provider": kw.get("provider", "p"),
                     **({} if "q_hash" not in kw else {"question_set_hash": kw["q_hash"]})})


def test_consistency_single_run_no_warnings():
    assert check_run_consistency([_run_pair("a")], "g") == []


def test_consistency_baseline_mismatch_raises():
    with pytest.raises(SystemExit, match="different baseline_arm"):
        check_run_consistency(
            [_run_pair("a", baseline="no-context"), _run_pair("b", baseline="baseline")], "g"
        )


@pytest.mark.outcome("cjm-question-set-hash-warn")
def test_consistency_hash_mismatch_warns():
    runs = [_run_pair("a", model="m1", q_hash="aaaa"), _run_pair("b", model="m2", q_hash="bbbb")]
    warns = check_run_consistency(runs, "g")
    (warning,) = [w for w in warns if "question_set_hash" in w]
    assert "a=aaaa" in warning and "b=bbbb" in warning  # the hashes that differ are named
    assert "common-question intersection will still be used" in warning


def test_consistency_identical_model_is_an_error_unless_pooling():
    # Runs sharing a configuration are replicates, not rival models. This used
    # to be a soft warning while the report still ranked them against each
    # other, which reported sampling noise as a model difference.
    runs = [_run_pair("a", model="same"), _run_pair("b", model="same")]
    with pytest.raises(SystemExit, match="--pool-replicates"):
        check_run_consistency(runs, "g")
    # Opting into pooling makes it acceptable.
    assert check_run_consistency(runs, "g", pool_replicates=True) == []


def test_consistency_ok_different_models():
    runs = [_run_pair("a", model="m1"), _run_pair("b", model="m2")]
    assert check_run_consistency(runs, "g") == []


# ---------------------------------------------------------------------------
# non_baseline_arms / prompt_digests
# ---------------------------------------------------------------------------


def test_non_baseline_arms_reads_the_arms_header():
    data = {"baseline_arm": "baseline", "arms": ["baseline", "skill:demo-skill"]}
    assert non_baseline_arms(data) == {"skill:demo-skill"}


def test_non_baseline_arms_falls_back_to_the_scored_record():
    """A legacy artifact carries no `arms` header; the arm names live in the records."""
    data = _eval_run([_q("Q1", arm="docs")])
    assert "arms" not in data
    assert non_baseline_arms(data) == {"docs"}


def test_non_baseline_arms_unions_over_every_record():
    """A judge failure drops an arm from one record; reading only the first would make the
    run look single-armed and switch the treatment-arm check off without saying so."""
    data = _eval_run(
        [
            {"id": "Q1", "scores": {"baseline": {"aggregate": 60}}},  # treatment call failed
            _q("Q2", arm="profile:concise_expert"),
        ]
    )
    assert non_baseline_arms(data) == {"profile:concise_expert"}


def test_non_baseline_arms_empty_when_nothing_but_baseline():
    assert non_baseline_arms({"baseline_arm": "baseline", "arms": ["baseline"]}) == set()


def test_prompt_digests_collects_the_stamped_prompt_hashes():
    data = _profile_run("run", digest="sha256:aaaa")
    assert prompt_digests(data, "profile:p") == {"sha256:aaaa"}
    assert prompt_digests(data, "profile:other") == set()


# ---------------------------------------------------------------------------
# treatment-arm integrity — pooling two different treatments is not a comparison
# ---------------------------------------------------------------------------


def _armed_run(run_id, arm, **kw):
    """One run whose single non-baseline arm is *arm*."""
    return (
        run_id,
        {
            "baseline_arm": "baseline",
            "arms": ["baseline", arm],
            "model": kw.get("model", run_id),
            "provider": "p",
        },
    )


def _profile_run(run_id, digest, arm="profile:p", n=2, **kw):
    """An artifact with *n* answers on *arm*, each stamping *digest*."""
    return {
        "baseline_arm": "baseline",
        "arms": ["baseline", arm],
        "model": kw.get("model", run_id),
        "provider": "p",
        "answers": [
            {
                "question_id": f"Q{i}",
                "arms": {
                    "baseline": {"answer": "a"},
                    arm: {"answer": "a", "metadata": {"profile_prompt_sha256": digest}},
                },
            }
            for i in range(n)
        ],
    }


def test_consistency_rejects_runs_measuring_different_treatments():
    """A `profile:` delta beside a `skill:` delta is confounded, not a model comparison."""
    runs = [_armed_run("a", "profile:concise_expert"), _armed_run("b", "skill:demo-skill")]
    with pytest.raises(SystemExit, match="different treatment arms"):
        check_run_consistency(runs, "regular")


def test_consistency_accepts_runs_sharing_one_treatment():
    runs = [_armed_run("a", "profile:concise_expert"), _armed_run("b", "profile:concise_expert")]
    assert check_run_consistency(runs, "regular") == []


def _scored_run(run_id, scored_arm, declared_extra=(), **kw):
    """A run declaring ``baseline + scored_arm + declared_extra`` but scored only on
    *scored_arm* — the shape a judge failure produces, where every call for the extra arm
    came back ``null``."""
    scores = {"baseline": {"aggregate": 5.0}, scored_arm: {"aggregate": 6.0}}
    scores.update({arm: None for arm in declared_extra})
    return (
        run_id,
        {
            "baseline_arm": "baseline",
            "arms": ["baseline", scored_arm, *declared_extra],
            "model": kw.get("model", run_id),
            "provider": "p",
            "evaluations": [
                {"question_id": f"Q{i}", "scores": scores} for i in range(2)
            ],
        },
    )


def test_consistency_rejects_different_treatments_even_when_one_run_is_ambiguous():
    """The guard must not switch itself off for the group.

    An earlier version required *every* run to resolve to exactly one non-baseline arm
    before it compared them, so a single run declaring an extra arm whose judge calls all
    failed dropped out of the comparison and took the check with it: a `profile:` delta
    then pooled with a `skill:` delta with exit 0 and a report on disk.
    """
    runs = [
        _scored_run("a", "profile:concise_expert"),
        _scored_run("b", "skill:demo-skill", declared_extra=("docs",)),
    ]
    with pytest.raises(SystemExit, match="different treatment arms"):
        check_run_consistency(runs, "regular")


def test_consistency_rejects_a_group_whose_third_run_is_baseline_only():
    """Same failure mode from the other side: a run with no non-baseline arm at all used to
    make the group unanimous by disappearing from it."""
    runs = [
        _scored_run("a", "profile:concise_expert"),
        _scored_run("b", "skill:demo-skill"),
        ("c", {"baseline_arm": "baseline", "arms": ["baseline"], "model": "c", "provider": "p"}),
    ]
    with pytest.raises(SystemExit, match="different treatment arms"):
        check_run_consistency(runs, "regular")


def test_an_unscored_arm_does_not_make_two_runs_look_different():
    """The flip side: the arm a run is *scored* on is what it measures, so a declared-but-
    unscored extra arm must not be read as a second treatment."""
    runs = [
        _scored_run("a", "profile:concise_expert", declared_extra=("docs",)),
        _scored_run("b", "profile:concise_expert"),
    ]
    assert check_run_consistency(runs, "regular") == []


def test_combined_arm_digests_are_read_from_the_nested_metadata():
    """A combined arm stamps profile provenance under metadata['treatments'], so a gate that
    only reads the top level finds nothing and calls two different prompts consistent."""
    def combined(run_id, digest):
        arm = "docs+profile:p"
        return (run_id, {
            "baseline_arm": "baseline",
            "arms": ["baseline", arm],
            "model": run_id,
            "provider": "p",
            "answers": [{
                "question_id": "Q0",
                "arms": {
                    "baseline": {"answer": "a"},
                    arm: {"answer": "a", "metadata": {
                        "arm_kind": "combined",
                        "treatments": [
                            {"name": "docs", "arm_kind": "docs"},
                            {"name": "profile:p", "profile_prompt_sha256": digest},
                        ],
                    }},
                },
            }],
        })

    runs = [combined("a", "sha256:" + "1" * 64), combined("b", "sha256:" + "2" * 64)]
    with pytest.raises(SystemExit, match="different prompt digests"):
        check_run_consistency(runs, "regular")


def test_pinned_treatment_arm_exempts_multi_arm_runs():
    """--treatment-arm names the arm explicitly, and extract_scores enforces it per run."""
    runs = [_armed_run("a", "docs"), _armed_run("b", "skill:s")]
    assert check_run_consistency(runs, "regular", treatment_arm="docs") == []


@pytest.mark.outcome("cjm-arm-digest-refused")
def test_consistency_rejects_one_arm_name_carrying_two_prompts():
    """Editing a profile in place keeps the arm name, so only the digest separates the cells."""
    runs = [
        ("a", _profile_run("a", digest="sha256:" + "1" * 64)),
        ("b", _profile_run("b", digest="sha256:" + "2" * 64)),
    ]
    with pytest.raises(SystemExit, match="different prompt digests"):
        check_run_consistency(runs, "regular")


def test_consistency_accepts_one_arm_name_with_one_prompt():
    runs = [
        ("a", _profile_run("a", digest="sha256:" + "1" * 64)),
        ("b", _profile_run("b", digest="sha256:" + "1" * 64)),
    ]
    assert check_run_consistency(runs, "regular") == []


def test_digest_check_ignores_runs_predating_the_stamp():
    """Artifacts scored before provenance stamping carry no digest — that is not a conflict."""
    stamped = _profile_run("a", digest="sha256:" + "1" * 64)
    legacy = _profile_run("b", digest="sha256:" + "1" * 64)
    for answer in legacy["answers"]:
        answer["arms"]["profile:p"].pop("metadata")
    assert check_run_consistency([("a", stamped), ("b", legacy)], "regular") == []


# ---------------------------------------------------------------------------
# extract_scores — partial / failed evaluations
# ---------------------------------------------------------------------------


def test_extract_scores_keeps_records_with_missing_aggregate():
    """Records are kept with None so the common-question intersection survives."""
    run = _eval_run([
        {"id": "Q1", "scores": {"baseline": {"aggregate": 60}, "with-ctx": {"aggregate": 80}}},
        {"id": "Q2", "scores": {"baseline": {}, "with-ctx": {"aggregate": 70}}},
        {"id": "Q3", "scores": {"baseline": {"aggregate": 50}, "with-ctx": {}}},
    ])
    scores = extract_scores(run)
    assert [s["question_id"] for s in scores] == ["Q1", "Q2", "Q3"]

    assert scores[0]["without_docs"] == 60
    assert scores[0]["with_docs"] == 80
    assert scores[0]["delta"] == 20

    # Missing baseline: carried as None rather than 0.0, which would inflate the
    # delta, and rather than dropped, which would shrink the intersection.
    assert scores[1]["without_docs"] is None
    assert scores[1]["with_docs"] == 70
    assert scores[1]["delta"] is None

    assert scores[2]["without_docs"] == 50
    assert scores[2]["with_docs"] is None
    assert scores[2]["delta"] is None


def test_missing_baseline_run_does_not_empty_the_intersection():
    """A run with no usable baselines must not zero out N for every other run."""
    good = _eval_run([_q("Q1"), _q("Q2")], model="good")
    broken = _eval_run([
        {"id": "Q1", "scores": {"baseline": {"error": "boom"}, "with-context": {"aggregate": 70}}},
        {"id": "Q2", "scores": {"baseline": {"error": "boom"}, "with-context": {"aggregate": 72}}},
    ], model="broken")

    lines = "\n".join(
        generate_section_report([("good", good), ("broken", broken)], ["good", "broken"], "test")
    )
    assert "Common questions evaluated: **2**" in lines


def _scored(qid, baseline, treatment, arm="with-context"):
    """Question spec allowing either side to be an error instead of a score."""
    return {
        "id": qid,
        "scores": {
            "baseline": {"aggregate": baseline} if baseline is not None else {"error": "boom"},
            arm: {"aggregate": treatment} if treatment is not None else {"error": "boom"},
        },
    }


def test_no_usable_score_renders_dash_not_zero():
    """An arm with no score must not print as 0, which reads as a real 0/100."""
    ok = _eval_run([_scored("Q1", 70, 75), _scored("Q2", 72, 78)], model="ok")
    bad = _eval_run([_scored("Q1", None, 75), _scored("Q2", None, 78)], model="bad")

    lines = "\n".join(generate_section_report([("ok", ok), ("bad", bad)], ["ok", "bad"], "t"))
    bad_row = next(ln for ln in lines.splitlines() if ln.startswith("| **bad** |") and "unscored" in ln)
    assert "| 0.0 |" not in bad_row
    assert "—" in bad_row


def test_by_difficulty_uses_paired_delta():
    """By-difficulty must not difference independently filtered means either."""
    # Q2 has a treatment score but no baseline. Differencing means gives +13.3;
    # the paired mean over Q1 and Q3 is +7.5.
    run = _eval_run([
        _scored("Q1", 70, 80),
        _scored("Q2", None, 90),
        _scored("Q3", 60, 65),
    ])
    lines = generate_section_report([("a", run)], ["a"], "t")
    diff_row = next(ln for ln in lines if ln.startswith("| easy |"))
    assert "+7.5" in diff_row
    assert "+13.3" not in diff_row


def test_difficulty_row_matches_header_column_count():
    """Runs differing in treatment coverage must not desync header and row widths."""
    ok = _eval_run([_scored("Q1", 70, 75), _scored("Q2", 72, 78)], model="ok")
    # No treatment score at all: previously flipped the header into baseline mode
    # via a clobbered has_treatment while rows still emitted two cells per run.
    bad = _eval_run([_scored("Q1", 70, None), _scored("Q2", 72, None)], model="bad")

    lines = generate_section_report([("ok", ok), ("bad", bad)], ["ok", "bad"], "t")
    header = next(ln for ln in lines if ln.startswith("| Difficulty |"))
    row = next(ln for ln in lines if ln.startswith("| easy |"))
    assert header.count("|") == row.count("|")


def test_delta_avg_is_mean_of_paired_deltas():
    """Delta must be the paired mean, not the difference of independently filtered means."""
    # Q2 has a treatment score but no baseline. Differencing arm means would give
    # (80+90)/2 - 60 = +25; the paired mean over Q1 alone is +20.
    run = _eval_run([
        {"id": "Q1", "scores": {"baseline": {"aggregate": 60}, "with-ctx": {"aggregate": 80}}},
        {"id": "Q2", "scores": {"baseline": {}, "with-ctx": {"aggregate": 90}}},
    ])
    lines = "\n".join(generate_section_report([("a", run)], ["a"], "test"))
    assert "**+20.0**" in lines
    assert "1 (1 unscored)" in lines


def test_extract_scores_skips_missing_question_id():
    run = _eval_run([
        {"id": "", "scores": {"baseline": {"aggregate": 60}, "with-ctx": {"aggregate": 80}}},
        {"id": "Q1", "scores": {"baseline": {"aggregate": 60}, "with-ctx": {"aggregate": 80}}},
    ])
    scores = extract_scores(run)
    assert len(scores) == 1
    assert scores[0]["question_id"] == "Q1"


def test_extract_scores_empty_evaluations():
    run = {"baseline_arm": "baseline", "evaluations": []}
    assert extract_scores(run) == []


def test_extract_scores_no_baseline_arm_returns_empty():
    run = {"evaluations": [{"question_id": "Q1", "scores": {"baseline": {"aggregate": 60}}}]}
    assert extract_scores(run) == []


def test_extract_scores_skips_question_where_baseline_absent_from_scores():
    """baseline_arm is set on the run but a specific question's scores dict
    doesn't contain that key at all — question must be silently skipped."""
    run = _eval_run([
        {"id": "Q1", "scores": {"skill:demo": {"aggregate": 70}}},  # no "baseline" key
        {"id": "Q2", "scores": {"baseline": {"aggregate": 50}, "skill:demo": {"aggregate": 70}}},
    ])
    scores = extract_scores(run)
    assert len(scores) == 1
    assert scores[0]["question_id"] == "Q2"


def test_extract_scores_all_failed_yields_no_usable_scores():
    """Every score None: records are kept for the intersection, but nothing is scored."""
    run = _eval_run([
        {"id": "Q1", "scores": {"baseline": {"aggregate": None}, "arm": {"aggregate": None}}},
        {"id": "Q2", "scores": {"baseline": {}, "arm": {}}},
    ])
    scores = extract_scores(run)
    assert [s["question_id"] for s in scores] == ["Q1", "Q2"]
    assert all(s["without_docs"] is None for s in scores)
    assert all(s["with_docs"] is None for s in scores)
    assert all(s["delta"] is None for s in scores)


def test_extract_scores_partial_coverage_diagnostic(capsys):
    """extract_scores should not crash on partial evaluations; missing questions
    surface as a smaller common-question set in the section report."""
    run_a = _eval_run([_q("Q1"), _q("Q2"), _q("Q3")], model="a")
    run_b = _eval_run([_q("Q1"), _q("Q3")], model="b")  # Q2 missing

    lines = generate_section_report([(("a", run_a)), ("b", run_b)], ["a", "b"], "test")
    # Common questions should be 2, not 3
    assert "Common questions evaluated: **2**" in "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_section_report — end-to-end smoke
# ---------------------------------------------------------------------------


def test_section_report_basic_output():
    run_a = _eval_run([_q("Q1", 60, 80), _q("Q2", 50, 70)], model="m-a")
    run_b = _eval_run([_q("Q1", 55, 75), _q("Q2", 45, 65)], model="m-b")
    lines = generate_section_report([("a", run_a), ("b", run_b)], ["a", "b"], "Regular")
    text = "\n".join(lines)
    assert "## Models Compared" in text
    assert "## Overall Summary" in text
    assert "## Head-to-Head" in text
    assert "Common questions evaluated: **2**" in text


def test_section_report_names_the_treatment_arm_it_scored():
    """Without the name, a persona report and a docs report are the same document."""
    run_a = _eval_run([_q("Q1", 60, 80, arm="profile:concise_expert")], model="m-a")
    run_b = _eval_run([_q("Q1", 55, 75, arm="profile:concise_expert")], model="m-b")
    text = "\n".join(generate_section_report([("a", run_a), ("b", run_b)], ["a", "b"], "Regular"))
    assert "| Run ID | Answer model | Judge model | Treatment arm |" in text
    assert "`profile:concise_expert`" in text
    assert "### By absolute quality (profile:concise_expert)" in text
    assert "Context arm" not in text


def test_section_report_falls_back_to_context_arm_when_the_arm_is_unknown():
    """A baseline-only run has no arm to name; the generic label must still render."""
    run_a = _eval_run([{"id": "Q1", "scores": {"baseline": {"aggregate": 60}}}], model="m-a")
    run_b = _eval_run([{"id": "Q1", "scores": {"baseline": {"aggregate": 55}}}], model="m-b")
    text = "\n".join(generate_section_report([("a", run_a), ("b", run_b)], ["a", "b"], "Regular"))
    assert "— (baseline only)" in text
    assert "Context arm" in text


def test_generate_combined_report_writes_file(tmp_path):
    run_a = _eval_run([_q("Q1"), _q("Q2")], model="m-a")
    run_b = _eval_run([_q("Q1"), _q("Q2")], model="m-b")
    out = tmp_path / "report.md"
    generate_combined_report([("a", run_a), ("b", run_b)], [], ["a", "b"], str(out))
    assert out.exists()
    content = out.read_text()
    assert "Model Comparison Report" in content
    assert "## Overall Summary" in content


def test_generated_stamp_without_zoneinfo_is_utc(tmp_path, monkeypatch):
    """Without zoneinfo the header says UTC, so it must print UTC, not local time."""
    from datetime import datetime, timedelta, timezone

    from dokimi.report import model_compare

    instant = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # A host at UTC-8: a naive now() reads 04:00.
            local = instant.astimezone(timezone(timedelta(hours=-8)))
            return instant.astimezone(tz) if tz else local.replace(tzinfo=None)

    monkeypatch.setattr(model_compare, "_TZ", None)
    monkeypatch.setattr(model_compare, "datetime", _Clock)
    out = tmp_path / "report.md"
    generate_combined_report([("m", _eval_run([_q("Q1")], model="m"))], [], ["m"], str(out))
    assert "_Generated: 2026-01-01 12:00 UTC_" in out.read_text().splitlines()


# --- shared statistics layer (eval/stats.py) ------------------------------

WITH = [60, 62, 58, 61, 59, 63]
WITHOUT = [50, 52, 48, 51, 49, 53]


def test_significance_test_matches_shared_paired_test():
    """model_compare must not re-derive what eval.stats already computes."""
    from dokimi.eval import stats
    from dokimi.report.model_compare import significance_test

    jittered = [w + (1 if i % 2 else -1) for i, w in enumerate(WITH)]
    mine = significance_test(jittered, WITHOUT)
    shared = stats.paired_test(jittered, WITHOUT)

    for key in ("n", "delta_mean", "delta_std", "cohens_d", "effect",
                "p_ttest", "p_wilcoxon", "significant"):
        assert mine[key] == shared[key], key


def test_significance_test_suppresses_verdict_on_constant_deltas():
    """Constant deltas make SciPy report p == 0; that must not read as certainty."""
    from dokimi.report.model_compare import significance_test

    sig = significance_test(WITH, WITHOUT)  # every delta is exactly +10
    assert sig["delta_mean"] == 10.0
    assert sig["p_ttest"] is None
    assert sig["significant"] is None
    assert sig["t_stat"] is None


def test_significance_test_reports_t_stat_for_varying_deltas():
    from dokimi.report.model_compare import significance_test

    jittered = [w + (1 if i % 2 else -1) for i, w in enumerate(WITH)]
    assert significance_test(jittered, WITHOUT)["t_stat"] is not None


def test_significance_test_requires_min_pairs():
    from dokimi.eval import stats
    from dokimi.report.model_compare import significance_test

    n = stats.MIN_PAIRS_FOR_TEST - 1
    assert significance_test(WITH[:n], WITHOUT[:n]) is None


def test_eval_report_significance_uses_sample_sd():
    """The eval_report copy divided by n, inflating d_z; it now matches the rest."""
    from dokimi.eval import stats
    from dokimi.report.eval_report import significance_test as er_sig

    evals = [
        {"with_docs": {"aggregate": w + (1 if i % 2 else -1)},
         "without_docs": {"aggregate": wo}}
        for i, (w, wo) in enumerate(zip(WITH, WITHOUT, strict=True))
    ]
    got = er_sig(evals)
    expected = stats.paired_test(
        [e["with_docs"]["aggregate"] for e in evals],
        [e["without_docs"]["aggregate"] for e in evals],
    )
    assert got["delta_std"] == expected["delta_std"]
    assert got["cohens_d"] == expected["cohens_d"]
    assert got["effect"] == expected["effect"]


def test_report_renders_na_instead_of_none_for_suppressed_pvalues(tmp_path):
    """A suppressed p-value must not leak the literal 'None' into the table."""
    from dokimi.report import model_compare

    run = {
        "arms": ["baseline", "docs"],
        "baseline_arm": "baseline",
        "evaluations": [
            {"question_id": f"q{i}", "question_text": f"Q{i}",
             "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 60.0}}}
            for i in range(6)
        ],
    }
    out = tmp_path / "report.md"
    model_compare.generate_combined_report([("m", run)], [], ["m"], out)
    text = out.read_text(encoding="utf-8")
    assert "| None |" not in text


# --- multiplicity correction across the compared runs ---------------------


def _run_with_delta(delta: float, n: int = 6) -> dict:
    """A run whose treatment beats baseline by ~delta, with some jitter."""
    return {
        "arms": ["baseline", "docs"],
        "baseline_arm": "baseline",
        "evaluations": [
            {
                "question_id": f"q{i}",
                "question_text": f"Q{i}",
                "scores": {
                    "baseline": {"aggregate": 50.0 + i},
                    "docs": {"aggregate": 50.0 + i + delta + (1 if i % 2 else -1)},
                },
            }
            for i in range(n)
        ],
    }


def _section(runs, correction="holm"):
    from dokimi.report.model_compare import generate_section_report

    ids = [rid for rid, _ in runs]
    return "\n".join(generate_section_report(runs, ids, "Regular", None, correction))


def test_section_report_adjusts_pvalues_across_runs():
    from dokimi.eval import stats
    from dokimi.report.model_compare import significance_test

    runs = [("a", _run_with_delta(3.0)), ("b", _run_with_delta(8.0))]
    text = _section(runs)

    assert "p (adjusted)" in text
    assert "unadjusted" not in text
    assert "Holm-Bonferroni" in text

    raw = [
        significance_test(
            [e["scores"]["docs"]["aggregate"] for e in data["evaluations"]],
            [e["scores"]["baseline"]["aggregate"] for e in data["evaluations"]],
        )["p_ttest"]
        for _, data in runs
    ]
    for p_adj in stats.adjust_pvalues(raw, method="holm"):
        assert str(p_adj) in text


def test_section_report_honors_bh():
    text = _section([("a", _run_with_delta(3.0)), ("b", _run_with_delta(8.0))], "bh")
    assert "Benjamini-Hochberg" in text


def test_correction_can_flip_a_borderline_verdict():
    """The point of the change: a raw-significant run may not survive the family."""
    from dokimi.eval import stats
    from dokimi.report.model_compare import significance_test

    runs = [(f"r{i}", _run_with_delta(1.4 + 0.1 * i)) for i in range(4)]
    raw = [
        significance_test(
            [e["scores"]["docs"]["aggregate"] for e in data["evaluations"]],
            [e["scores"]["baseline"]["aggregate"] for e in data["evaluations"]],
        )["p_ttest"]
        for _, data in runs
    ]
    adjusted = stats.adjust_pvalues(raw, method="holm")
    assert all(p is not None for p in raw)
    # Correction can only ever raise a p-value, never lower it.
    assert all(a >= r for a, r in zip(adjusted, raw, strict=True))
    assert any(a > r for a, r in zip(adjusted, raw, strict=True))


def test_unrunnable_test_stays_na_and_does_not_inflate_the_family():
    """Constant deltas yield no p-value; the row must read N/A, not 'No'."""
    flat = {
        "arms": ["baseline", "docs"],
        "baseline_arm": "baseline",
        "evaluations": [
            {"question_id": f"q{i}", "question_text": f"Q{i}",
             "scores": {"baseline": {"aggregate": 50.0 + i},
                        "docs": {"aggregate": 60.0 + i}}}   # every delta is +10
            for i in range(6)
        ],
    }
    text = _section([("flat", flat)])
    assert "| None |" not in text

    section = text.split("## Statistical Significance")[1]
    row = next(line for line in section.splitlines() if line.startswith("| **flat** |"))
    # Delta and effect band are all this data supports: no p-value, no d_z,
    # no verdict.
    assert row == "| **flat** | +10.0 | N/A | N/A | N/A | N/A | unknown | N/A |"
    assert "adjusted across the 0 run(s)" in section


def test_cli_exposes_stats_correction_for_model_compare():
    from dokimi.cli import build_parser
    from dokimi.eval import stats

    for method in stats.CORRECTIONS:
        args = build_parser().parse_args(
            ["report", "model-compare", "--regular-runs", "a.json",
             "--run-ids", "a", "--out", "out.md", "--stats-correction", method]
        )
        assert args.stats_correction == method

    assert build_parser().parse_args(
        ["report", "model-compare", "--regular-runs", "a.json",
         "--run-ids", "a", "--out", "out.md"]
    ).stats_correction == "holm"

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["report", "model-compare", "--regular-runs", "a.json",
             "--run-ids", "a", "--out", "out.md", "--stats-correction", "bonferroni"]
        )


def test_pooled_section_reports_dropped_questions_and_what_pooling_means():
    from dokimi.report.model_compare import generate_section_report

    full = [_q("Q1"), _q("Q2")]
    runs = [("rep1", _eval_run(full)), ("rep2", _eval_run([_q("Q1")]))]
    text = "\n".join(generate_section_report(runs, ["rep1", "rep2"], "Golden",
                                             pool_replicates=True))
    assert "1 question(s) dropped for not being scored in every replicate" in text
    assert "mean of k runs" in text


_V1 = "treatment_material.v1"


def _stamped(run_id, stamp):
    rid, data = _scored_run(run_id, "skill:demo-skill")
    if stamp is not None:
        data["judge_context"] = stamp
    return rid, data


@pytest.mark.parametrize(
    "stamp_a, stamp_b",
    [(None, _V1), (_V1, None), (_V1, "treatment_material.v2")],
)
def test_consistency_refuses_judge_context_that_differs(stamp_a, stamp_b):
    """Unstamped runs were judged on report snippets; pooling them with stamped runs
    puts two judge protocols in one comparison."""
    runs = [_stamped("a", stamp_a), _stamped("b", stamp_b)]
    with pytest.raises(SystemExit, match="judge_context"):
        check_run_consistency(runs, "regular")


@pytest.mark.parametrize("other", [None, _V1, _V1 + "+snippet_fallback"])
def test_consistency_refuses_a_mixed_run_even_against_another_mixed_one(other):
    """Two mixed runs fell back on different answers; they share a label, not evidence."""
    runs = [_stamped("a", _V1 + "+snippet_fallback"), _stamped("b", other)]
    with pytest.raises(SystemExit, match="pools with nothing"):
        check_run_consistency(runs, "regular")


def test_consistency_message_says_to_rerun_not_rejudge():
    runs = [_stamped("a", None), _stamped("b", _V1)]
    with pytest.raises(SystemExit, match="Re-run answering and judging"):
        check_run_consistency(runs, "regular")


@pytest.mark.parametrize("stamp", [None, _V1])
def test_consistency_allows_judge_context_that_matches(stamp):
    runs = [_stamped("a", stamp), _stamped("b", stamp)]
    assert check_run_consistency(runs, "regular") == []


def test_consistency_ignores_the_stamp_on_unjudged_runs():
    """Only judged runs are stamped, so an unjudged one cannot disagree."""
    runs = [_stamped("a", _V1), _run_pair("b", model="other")]
    check_run_consistency(runs, "regular")


MISSING = "\u2014"  # the report's marker for an unknown value


def _costed_run(cost, tokens=100, latency=2.0, calls=4):
    data = _eval_run([_q("Q1"), _q("Q2")])
    data["cost_summary"] = {"baseline": {
        "n": 2, "total_cost_usd": cost, "cost_known_n": calls, "total_llm_calls": calls,
        "prompt_tokens": tokens, "completion_tokens": tokens // 2, "total_tokens": tokens + tokens // 2,
        "mean_latency_sec": latency,
    }}
    return data


def _resource_row(text, label):
    prefixes = (f"| **{label}** | $", f"| **{label}** | {MISSING}")
    return next(line for line in text.splitlines() if line.startswith(prefixes))


def test_pooled_resource_row_sums_cost_and_tokens_over_replicates():
    runs = [("rep1", _costed_run(1.0, latency=1.0)), ("rep2", _costed_run(2.0, latency=2.0)),
            ("rep3", _costed_run(3.0, latency=6.0))]
    text = "\n".join(generate_section_report(runs, ["rep1", "rep2", "rep3"], "Golden",
                                             pool_replicates=True))
    assert _resource_row(text, "rep[n=3]") == f"| **rep[n=3]** | $6.0000 | 300 | 150 | 450 | 3.00s | {MISSING} |"
    assert "total cost, calls and tokens of its k runs" in text


def test_pooled_resource_row_keeps_a_field_missing_when_a_replicate_lacks_it():
    lacking = _costed_run(2.0)
    del lacking["cost_summary"]["baseline"]["total_cost_usd"]
    runs = [("rep1", _costed_run(1.0)), ("rep2", lacking), ("rep3", _costed_run(3.0))]
    text = "\n".join(generate_section_report(runs, ["rep1", "rep2", "rep3"], "Golden",
                                             pool_replicates=True))
    assert _resource_row(text, "rep[n=3]").startswith(f"| **rep[n=3]** | {MISSING} | 300 | 150 | 450 |")


def test_single_run_resource_row_is_unchanged_and_has_no_pooling_footnote():
    text = "\n".join(generate_section_report([("solo", _costed_run(1.5))], ["solo"], "Golden",
                                             pool_replicates=True))
    assert _resource_row(text, "solo") == f"| **solo** | $1.5000 | 100 | 50 | 150 | 2.00s | {MISSING} |"
    assert "total cost, calls and tokens of its k runs" not in text


def test_pooled_cache_hit_ratio_is_weighted_by_prompt_tokens():
    from dokimi.report.model_compare import pool_cost_summaries

    pooled = pool_cost_summaries([{"baseline": {"prompt_tokens": 100, "cache_hit_ratio": 0.5}},
                                  {"baseline": {"prompt_tokens": 300, "cache_hit_ratio": 0.0}}])
    assert pooled["baseline"]["cache_hit_ratio"] == 0.125
    assert pooled["baseline"]["total_cost_usd"] is None
