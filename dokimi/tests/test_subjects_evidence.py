# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""skill_evidence.v1: a subject scorecard rendered for publishing next to the skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifact_fixtures import artifact_fixture
from dokimi.artifacts import load_artifact, save_artifact
from dokimi.subjects.evidence import build_skill_evidence

DIGEST = "sha256:" + "a" * 64
FIXTURE = Path(__file__).parent / "fixtures" / "artifacts" / "subject_scorecard.v2.json"


def _significance(delta: float, *, significant: bool) -> dict:
    return {"ci_confidence": 0.95, "ci_lo": delta - 1.5, "ci_hi": delta + 1.5, "ci_method": "student_t",
            "correction": "holm", "delta_mean": delta, "n": 6, "p_adjusted": 0.001 if significant else 0.4,
            "significant_adjusted": significant}


def _scorecard(**subject) -> dict:
    per_arm = {
        "baseline": {"avg_aggregate": 50.0, "delta_vs_baseline": None, "n": 6},
        "skill:s": {"avg_aggregate": 62.0, "delta_vs_baseline": 12.0, "n": 6,
                    "significance": _significance(12.0, significant=True)},
        "placebo": {"avg_aggregate": 51.0, "delta_vs_baseline": 1.0, "n": 6,
                    "significance": _significance(1.0, significant=False)},
    }
    # The committed fixture, so the base is a scorecard a producer wrote; only subject and cells change.
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base["subject"] = {"id": "s", "kind": "skill", "ref": "skills/s", "ref_digest": "sha256:" + "b" * 64,
                       "distribution_digest": DIGEST, "members": [], **subject}
    base["cells"] = [{"product": "oneTBB", "questions": "q.json", "matrix_cell": "c1", "artifact": "c1.json",
                      "model": "m", "provider": "p", "harness": "arms-runner", "baseline_arm": "baseline",
                      "plugin_set": "none",
                      "summary": {"per_arm": per_arm}, "cost_summary": {}}]
    return artifact_fixture("subject_scorecard", base, closed=False)


def test_each_non_baseline_arm_is_one_result_read_from_the_scorecard(tmp_path: Path):
    evidence = build_skill_evidence(_scorecard(), source="sc.json")

    assert evidence["subject"]["digest"] == DIGEST
    assert evidence["eval_set"]["digest"] == _scorecard()["suite"]["question_set_hash"]
    by_arm = {row["arm"]: row for row in evidence["results"]}
    assert set(by_arm) == {"skill:s", "placebo"}
    skill = by_arm["skill:s"]
    assert (skill["model"], skill["harness"], skill["n"]) == ("m", "arms-runner", 6)
    assert (skill["n"], skill["delta"]) == (6, 12.0)
    assert skill["ci"] == {"lo": 10.5, "hi": 13.5, "confidence": 0.95, "method": "student_t"}
    assert (skill["p_value"], skill["p_correction"], skill["verdict"]) == (0.001, "holm", "improved")
    assert by_arm["placebo"]["verdict"] == "inconclusive"
    # Not recorded by the run, so not filled in.
    assert skill["effort"] is None and skill["judge_model"] is None
    save_artifact("skill_evidence", evidence, tmp_path / "e.json")  # validates against the schema


@pytest.mark.parametrize("delta, significant, verdict", [
    (-3.0, True, "regressed"), (-3.0, False, "inconclusive"), (0.0, True, "inconclusive"),
])
def test_only_a_corrected_significant_delta_gets_a_direction(delta, significant, verdict):
    scorecard = _scorecard()
    row = scorecard["cells"][0]["summary"]["per_arm"]["skill:s"]
    row["delta_vs_baseline"] = delta
    row["significance"] = _significance(delta, significant=significant)

    by_arm = {r["arm"]: r for r in build_skill_evidence(scorecard)["results"]}
    assert by_arm["skill:s"]["verdict"] == verdict


def test_a_subject_with_no_distribution_digest_is_flagged_not_guessed():
    evidence = build_skill_evidence(_scorecard(distribution_digest=None))

    assert evidence["subject"]["digest"] is None
    assert any("distribution_digest" in w for w in evidence["warnings"])


def test_subjects_evidence_writes_the_document_next_to_the_scorecard(tmp_path: Path, monkeypatch):
    from dokimi.cli import main

    scorecard = tmp_path / "subject-scorecard.json"
    save_artifact("subject_scorecard", _scorecard(), scorecard)

    monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(scorecard)])
    main()

    out = load_artifact("skill_evidence", tmp_path / "skill-evidence.json")
    assert out["source"] == {"kind": "subject_scorecard.v2", "path": "subject-scorecard.json"}
    assert len(out["results"]) == 2


def test_subjects_evidence_refuses_a_file_that_is_not_a_scorecard(tmp_path: Path, monkeypatch):
    from dokimi.cli import main

    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"schema_version": "subject_scorecard.v2"}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(bad)])
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("cells", [
    [None],
    [{"summary": []}],
    [{"summary": {"per_arm": []}}],
    [{"summary": {"per_arm": {"skill:s": 5}}}],
    [{"summary": {"per_arm": {"skill:s": {"significance": "x"}}}}],
])
def test_subjects_evidence_refuses_a_scorecard_with_a_wrong_typed_cell(tmp_path: Path, monkeypatch, cells):
    from dokimi.cli import main

    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({**_scorecard(), "cells": cells}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(bad)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_subjects_evidence_refuses_a_nested_statistic_the_evidence_schema_rejects(tmp_path: Path, monkeypatch):
    # The scorecard schema accepts a string n and rendering copies it through; only the evidence
    # schema rejects it at save time. That must be an input diagnostic (exit 2), not a traceback.
    from dokimi.cli import main

    card = _scorecard()
    card["cells"][0]["summary"]["per_arm"]["skill:s"]["significance"]["n"] = "6"
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps(card), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(bad)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not (tmp_path / "skill-evidence.json").exists()


def test_n_and_delta_are_the_paired_tests_not_the_arms_own_counts():
    scorecard = _scorecard()
    row = scorecard["cells"][0]["summary"]["per_arm"]["skill:s"]
    row["n"], row["delta_vs_baseline"] = 10, 20.0  # the arm alone; 3 pairs behind the test
    row["significance"] = {**_significance(4.0, significant=True), "n": 3}

    skill = {r["arm"]: r for r in build_skill_evidence(scorecard)["results"]}["skill:s"]
    assert (skill["n"], skill["delta"]) == (3, 4.0)


def test_the_recorded_baseline_wins_over_an_arm_that_has_no_pairs():
    scorecard = _scorecard()
    per_arm = scorecard["cells"][0]["summary"]["per_arm"]
    # Every answer of this arm failed: no delta, no significance, like a baseline row; listed first.
    scorecard["cells"][0]["summary"]["per_arm"] = {"broken": {"avg_aggregate": None, "delta_vs_baseline": None,
                                                              "n": 0}, **per_arm}

    arms = {r["arm"] for r in build_skill_evidence(scorecard)["results"]}
    assert arms == {"broken", "skill:s", "placebo"}

    del scorecard["cells"][0]["baseline_arm"]  # older cell: two candidates, so the cell is skipped
    evidence = build_skill_evidence(scorecard)
    assert evidence["results"] == []
    assert any("no single baseline arm" in w for w in evidence["warnings"])


def test_subjects_evidence_refuses_to_overwrite_the_scorecard_or_publish_nothing(tmp_path: Path, monkeypatch):
    from dokimi.cli import main

    scorecard = tmp_path / "sc.json"
    save_artifact("subject_scorecard", _scorecard(), scorecard)
    before = scorecard.read_bytes()
    monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(scorecard), "--out", str(scorecard)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and scorecard.read_bytes() == before

    empty = _scorecard()
    empty["cells"] = []
    save_artifact("subject_scorecard", empty, scorecard)
    monkeypatch.setattr("sys.argv", ["dokimi", "subjects", "evidence", str(scorecard)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and not (tmp_path / "skill-evidence.json").exists()


def test_the_rollup_cells_carry_the_baseline_arm(tmp_path: Path):
    from dokimi.eval.matrix_rollup import build_matrix_rollup

    rollup = build_matrix_rollup(library_name="l", out_dir=tmp_path, compute_plugin_deltas=False,
                                 cell_artifacts=[("c1", tmp_path / "c1.json", {"baseline_arm": "none"})])
    assert rollup["cells"][0]["baseline_arm"] == "none"


def test_a_significance_flag_that_is_not_a_boolean_gives_no_direction():
    scorecard = _scorecard()
    row = scorecard["cells"][0]["summary"]["per_arm"]["skill:s"]
    row["significance"] = {**_significance(12.0, significant=True), "significant_adjusted": "false"}

    by_arm = {r["arm"]: r for r in build_skill_evidence(scorecard)["results"]}
    assert by_arm["skill:s"]["verdict"] == "inconclusive"


def test_the_scorecard_cells_carry_the_baseline_arm_of_the_rollup(tmp_path: Path):
    from dokimi.subjects import build_subject_scorecard, load_subject

    (tmp_path / "subject.toml").write_text(
        '[subject]\nid = "s"\nkind = "doc-source"\nref = "local:docs"\n\n'
        '[suite]\nproducts = ["oneTBB"]\nquestions = ["questions.json"]\n', encoding="utf-8")
    rollup = {"cells": [{"matrix_cell": "c1", "artifact": "c1.json", "model": "m", "provider": "p",
                         "harness": "arms-runner", "baseline_arm": "none", "plugin_set": "none",
                         "summary": {}, "cost_summary": {}}], "plugin_deltas": [], "warnings": []}
    scorecard = build_subject_scorecard(
        load_subject(tmp_path / "subject.toml"),
        awareness_runs=[{"product": "oneTBB", "questions": "questions.json", "matrix_rollup": "m.json",
                         "rollup": rollup}],
        out_dir=tmp_path)
    assert scorecard["cells"][0]["baseline_arm"] == "none"
