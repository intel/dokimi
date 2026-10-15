# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for versioned, schema-validated artifact I/O."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import (
    SCHEMA_VERSIONS,
    SUPERSEDED_VERSIONS,
    ArtifactValidationError,
    load_artifact,
    save_artifact,
    stamp,
    validate_artifact,
)

REPO = Path(__file__).resolve().parents[1]


def test_all_kinds_have_schema_files():
    for kind, version in SCHEMA_VERSIONS.items():
        schema = REPO / "dokimi" / "schemas" / f"{version}.json"
        assert schema.exists(), f"missing schema for {kind}: {schema}"


# One minimal but realistic payload per pipeline artifact kind. These replace
# the captured product runs the suite used to load from data/: no answer run or
# judge output is committed any more, so the contract under test is the envelope
# plus the record shape, which a synthetic payload locks just as well.
PIPELINE_PAYLOADS: dict[str, dict] = {
    "questions": {
        "library": "demo",
        "questions": [
            {
                "id": "q1",
                "question": "How do I install the library?",
                "category": "installation",
                "difficulty": "easy",
                "level": "triage",
                "expected_topics": ["install"],
            }
        ],
    },
    "answers": {
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "demo-model",
        "provider": "openai",
        "total_questions": 1,
        "answers": [
            {
                "question_id": "q1",
                "question_text": "How do I install the library?",
                "library_name": "demo",
                "with_docs": {"answer": "pip install demo", "tokens": 5},
                "without_docs": {"answer": "unsure", "tokens": 2},
            }
        ],
    },
    "eval": {
        "evaluated_at": "2026-01-01T00:00:00Z",
        "judge_model": "demo-judge",
        "judge_provider": "openai",
        "total_evaluations": 1,
        "evaluations": [
            {
                "question_id": "q1",
                "question_text": "How do I install the library?",
                "with_docs": {"aggregate": 80.0},
                "without_docs": {"aggregate": 60.0},
                "delta": 20.0,
            }
        ],
    },
}


@pytest.mark.parametrize("kind", sorted(PIPELINE_PAYLOADS))
def test_pipeline_artifact_kinds_round_trip(kind, tmp_path):
    """Every pipeline artifact kind must survive save -> load with validation on.

    Locks the same contract the committed product fixtures used to lock: the
    stamped ``schema_version`` envelope and the per-record shape each schema in
    ``dokimi/schemas/`` requires.
    """
    payload = PIPELINE_PAYLOADS[kind]
    out = tmp_path / f"{kind}.json"

    save_artifact(kind, payload, out)  # validates on the way out
    loaded = load_artifact(kind, out)  # and again on the way in

    assert loaded["schema_version"] == SCHEMA_VERSIONS[kind]
    # The record list is non-empty, so the item subschema is really exercised
    # (an empty list validates against every one of these schemas).
    records = loaded[{"questions": "questions", "answers": "answers", "eval": "evaluations"}[kind]]
    assert len(records) == 1
    assert records[0]["question_id" if kind != "questions" else "id"] == "q1"


RUBRIC_LEVELS = ("triage", "diagnosis", "end_to_end")


def test_the_two_axis_rubric_round_trips(tmp_path):
    """Lock the difficulty/level two-axis rubric of docs/difficulty-rubric.md.

    A question set may carry a ``level`` from the schema enum on every question, and the
    ``negative_case`` flag is the boolean ``True`` convention inside the free-form ``metadata``
    object. No question set is committed any more, so
    the rubric is asserted on a payload written here rather than on a captured product set —
    which also lets the negative case be covered, and the enum be shown to be enforced.
    """
    payload = {
        "library": "demo",
        "questions": [
            {"id": f"q{i}", "question": f"Question {i}?", "difficulty": "intermediate", "level": level}
            for i, level in enumerate(RUBRIC_LEVELS, start=1)
        ]
        + [
            {
                "id": "q4",
                "question": "Should I use the GPU path for a 10-element array?",
                "difficulty": "intermediate",
                "level": "triage",
                "metadata": {"negative_case": True},
            }
        ],
    }
    out = tmp_path / "golden.json"
    save_artifact("questions", payload, out)

    questions = load_artifact("questions", out)["questions"]
    assert len(questions) == 4
    for q in questions:
        assert q.get("level") in RUBRIC_LEVELS, f"{q['id']}: bad/missing level {q.get('level')!r}"
        flag = q.get("metadata", {}).get("negative_case")
        assert flag in (None, True), f"{q['id']}: negative_case must be true or absent, got {flag!r}"
    assert [q for q in questions if q.get("metadata", {}).get("negative_case")], (
        "the negative case did not survive the round trip"
    )


def test_a_level_outside_the_rubric_is_rejected(tmp_path):
    """The enum is what makes the rubric a contract rather than a convention."""
    with pytest.raises(ArtifactValidationError, match="level: 'deep_dive' is not one of"):
        save_artifact(
            "questions",
            {"questions": [{"id": "q1", "question": "Q?", "level": "deep_dive"}]},
            tmp_path / "bad.json",
        )


def test_stamp_sets_version():
    data = stamp("questions", {"questions": []})
    assert data["schema_version"] == "questions.v2"


def test_save_roundtrip_stamps_and_validates(tmp_path):
    out = tmp_path / "q.json"
    save_artifact("questions", {"questions": [{"id": "q1", "question": "What is X?"}]}, out)
    loaded = load_artifact("questions", out)
    assert loaded["schema_version"] == "questions.v2"
    assert loaded["questions"][0]["id"] == "q1"


def test_save_rejects_invalid(tmp_path):
    with pytest.raises(ArtifactValidationError):
        save_artifact("answers", {"not_answers": []}, tmp_path / "bad.json")


def test_validate_missing_required_field():
    with pytest.raises(ArtifactValidationError, match="evaluations"):
        validate_artifact("eval", {"evaluated_at": "now"})


def test_question_item_requires_its_versions_text_key():
    with pytest.raises(ArtifactValidationError, match="'question' is a required property"):
        validate_artifact("questions", {"questions": [{"id": "q1"}]})
    with pytest.raises(ArtifactValidationError, match="'text' is a required property"):
        validate_artifact("questions", {"schema_version": "questions.v1",
                                        "questions": [{"id": "q1", "question": "Q?"}]})


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        validate_artifact("nope", {})


def test_arms_schema_accepts_built_output():
    out = {
        "arms": ["baseline", "with_docs"],
        "answers": [],
        "summary": {"per_arm": {"baseline": {"avg_aggregate": 70.0, "n": 3}}},
    }
    validate_artifact("arms", out)


def test_save_does_not_mutate_caller():
    data = {"questions": [{"id": "q1", "question": "x"}]}
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        save_artifact("questions", data, os.path.join(d, "q.json"))
    assert "schema_version" not in data  # caller dict untouched


def test_save_puts_the_current_version_first_and_returns_what_it_wrote(tmp_path):
    out = tmp_path / "deep" / "q.json"
    written = save_artifact("questions", {"questions": [], "schema_version": "questions.v0"}, out)
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == written
    assert list(on_disk) == ["schema_version", "questions"]
    assert on_disk["schema_version"] == SCHEMA_VERSIONS["questions"]
    assert [p.name for p in out.parent.iterdir()] == ["q.json"]  # the .tmp sibling is gone


def test_a_rejected_save_leaves_the_target_alone_and_keeps_the_payload(tmp_path):
    out = tmp_path / "answers.json"
    save_artifact("answers", {"answers": [{"question_id": "q1"}]}, out)
    before = out.read_text(encoding="utf-8")
    with pytest.raises(ArtifactValidationError, match=r"(?s)answers/0: 'question_id'.*answers\.json\.rejected"):
        save_artifact("answers", {"answers": [{"question_text": "Q?"}]}, out)
    assert out.read_text(encoding="utf-8") == before
    kept = json.loads((tmp_path / "answers.json.rejected").read_text(encoding="utf-8"))
    assert kept["answers"] == [{"question_text": "Q?"}]


def test_save_rejects_an_unknown_kind_before_writing(tmp_path):
    with pytest.raises(KeyError):
        save_artifact("nope", {}, tmp_path / "x.json")
    assert not list(tmp_path.iterdir())


def test_schema_errors_by_name_and_by_path(tmp_path):
    from dokimi.artifacts import SCHEMA_DIR, schema_errors

    bad = {"claims": [{"file": 1}]}
    by_name = schema_errors("repo_static.v1", bad)
    assert by_name and by_name == schema_errors(SCHEMA_DIR / "repo_static.v1.json", bad)
    assert all(": " in e for e in by_name)
    assert schema_errors("repo_static.v1", {"schema": "repo_static.v1", "claims": []}) == []
    with pytest.raises(OSError):
        schema_errors(tmp_path / "missing.json", {})


def test_schema_version_const_rejects_wrong_value():
    with pytest.raises(ArtifactValidationError):
        validate_artifact("questions", {"schema_version": "questions.v3", "questions": []})


def _scorecard_v1():
    """A subject scorecard written before the novelty bump: no ``novelty`` block at all."""
    return {
        "schema_version": "subject_scorecard.v1",
        "generated_at": "2026-01-01T00:00:00Z",
        "subject": {"id": "s", "kind": "doc-source", "ref": "local:docs"},
        "suite": {"products": ["oneTBB"], "questions": ["q.json"]},
        "baseline": "baseline",
        "awareness": {"arm_specs": [], "runs": []},
        "work": {"status": "not_run", "arm_specs": [], "tasks": []},
        "cells": [],
        "plugin_deltas": [],
        "warnings": [],
    }


def test_all_superseded_versions_have_schema_files():
    for kind, versions in SUPERSEDED_VERSIONS.items():
        assert kind in SCHEMA_VERSIONS
        for version in versions:
            assert (REPO / "dokimi" / "schemas" / f"{version}.json").exists()


def test_a_superseded_scorecard_still_loads(tmp_path):
    # A bump is additive by policy, so an artifact written before it is still a valid artifact.
    validate_artifact("subject_scorecard", _scorecard_v1())
    path = tmp_path / "old.json"
    path.write_text(json.dumps(_scorecard_v1()), encoding="utf-8")
    assert load_artifact("subject_scorecard", path)["schema_version"] == "subject_scorecard.v1"


def test_rewriting_a_superseded_payload_must_state_its_novelty_status(tmp_path):
    # `stamp` always writes the current version, and v2 requires `novelty`, so a v1 payload cannot
    # be re-saved by silently omitting it: an absent block would read as "no novelty" rather than
    # "not measured". Adding the explicit not_run block is what makes it a v2 artifact.
    path = tmp_path / "new.json"
    with pytest.raises(ArtifactValidationError, match="novelty"):
        save_artifact("subject_scorecard", _scorecard_v1(), path)
    upgraded = dict(_scorecard_v1(), novelty={"status": "not_run", "cells": []})
    save_artifact("subject_scorecard", upgraded, path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "subject_scorecard.v2"


def test_an_unknown_version_is_still_rejected_rather_than_skipping_validation():
    # The fallback is the *current* schema, whose const rejects the string: a typo or a future
    # version must not read as "no schema to check against".
    with pytest.raises(ArtifactValidationError):
        validate_artifact(
            "subject_scorecard", dict(_scorecard_v1(), schema_version="subject_scorecard.v9")
        )


def _factorial_summary():
    """Real ``_summarize`` output for a 2x2 design, so the schema is checked
    against what the runner actually emits rather than a hand-written stub."""
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.treatments import BaselineTreatment

    arms = ["baseline", "docs", "skill:x", "docs+skill:x"]
    base = [50, 52, 48, 51, 49, 53]
    jitter = [1, -1, 2, -2, 1, -1]
    evaluations = [
        {
            "question_id": f"q{i}",
            "scores": {
                "baseline": {"aggregate": b},
                "docs": {"aggregate": b + 2 + j},
                "skill:x": {"aggregate": b + 12 - j},
                "docs+skill:x": {"aggregate": b + 6 + j},
            },
        }
        for i, (b, j) in enumerate(zip(base, jitter, strict=True))
    ]
    runner = ArmRunner([BaselineTreatment(name) for name in arms])
    return arms, runner._summarize(evaluations, "baseline")


def test_arms_schema_accepts_significance_and_interactions():
    arms, summary = _factorial_summary()
    assert summary["per_arm"]["docs"]["significance"]["correction"] == "holm"
    assert len(summary["interactions"]) == 1
    validate_artifact("arms", {"arms": arms, "answers": [], "summary": summary})


def test_real_factorial_output_validates_and_then_renders():
    """Schema-valid is not the same as renderable.

    Every other stats test stops at ``validate_artifact``, so a formatter that
    raises on a field the runner really emits — a ``None`` Cohen's d_z, a
    missing adjusted verdict — would pass the suite. This runs the whole seam:
    real ``_summarize`` output, validated, then rendered to Markdown.
    """
    from dokimi.report.arms_report import render_arms_report

    arms, summary = _factorial_summary()
    artifact = {
        "library_name": "onetbb", "model": "m", "provider": "openai",
        "arms": arms, "baseline_arm": "baseline", "total_questions": 6,
        "answers": [], "summary": summary,
    }
    validate_artifact("arms", artifact)
    md = render_arms_report(artifact)
    assert "## Paired significance vs baseline" in md
    assert "## Factorial interaction" in md
    # Both families name themselves, so a reader can tell what was corrected
    # together rather than assuming one pooled correction.
    assert "treatment arm(s)" in md and "interaction contrast(s)" in md


def test_arms_schema_still_accepts_artifact_without_stats():
    """Pre-existing arms.v1 artifacts predate these keys and must stay valid."""
    validate_artifact("arms", {
        "arms": ["baseline", "docs"],
        "answers": [],
        "summary": {"per_arm": {"docs": {"avg_aggregate": 70.0, "n": 3,
                                        "delta_vs_baseline": 4.0}}},
    })


def test_arms_schema_rejects_bad_effect_label():
    """The new block is constrained, not merely documented."""
    with pytest.raises(ArtifactValidationError):
        validate_artifact("arms", {
            "arms": ["baseline", "docs"],
            "answers": [],
            "summary": {"per_arm": {"docs": {"significance": {"effect": "enormous"}}}},
        })


def test_arms_schema_rejects_bad_correction_name():
    with pytest.raises(ArtifactValidationError):
        validate_artifact("arms", {
            "arms": ["baseline", "docs"],
            "answers": [],
            "summary": {"per_arm": {"docs": {"significance": {"correction": "bonferroni"}}}},
        })


def test_arms_schema_rejects_non_array_interactions():
    with pytest.raises(ArtifactValidationError):
        validate_artifact("arms", {
            "arms": ["baseline", "docs"],
            "answers": [],
            "summary": {"interactions": {"interaction": -8.0}},
        })


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows os.replace refuses a destination another thread is replacing")
def test_concurrent_saves_to_one_path_each_publish_a_whole_payload(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    out = tmp_path / "answers.json"
    payloads = [{"answers": [{"question_id": f"t{t}-{i}"} for i in range(200)]} for t in range(6)]

    def hammer(payload):
        for _ in range(30):
            save_artifact("answers", payload, out)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for f in [pool.submit(hammer, p) for p in payloads]:
            f.result()  # a shared tmp name made the losing writer raise FileNotFoundError
    final = json.loads(out.read_text(encoding="utf-8"))["answers"]
    assert final in [p["answers"] for p in payloads]
    assert [p.name for p in tmp_path.iterdir()] == ["answers.json"]


def test_a_crash_at_the_rename_keeps_the_previous_file_and_leaves_no_tmp(tmp_path, monkeypatch):
    out = tmp_path / "answers.json"
    save_artifact("answers", {"answers": [{"question_id": "old"}]}, out)

    def boom(src, dst):
        raise OSError("disk gone")

    monkeypatch.setattr("dokimi.artifacts.os.replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        save_artifact("answers", {"answers": [{"question_id": "new"}]}, out)
    assert json.loads(out.read_text(encoding="utf-8"))["answers"] == [{"question_id": "old"}]
    assert [p.name for p in tmp_path.iterdir()] == ["answers.json"]


def test_a_good_save_clears_a_stale_rejected_payload(tmp_path):
    out = tmp_path / "answers.json"
    with pytest.raises(ArtifactValidationError):
        save_artifact("answers", {"answers": [{"question_text": "Q?"}]}, out)
    assert (tmp_path / "answers.json.rejected").exists()
    (tmp_path / "other.json.rejected").write_text("{}", encoding="utf-8")

    save_artifact("answers", {"answers": [{"question_id": "q1"}]}, out)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["answers.json", "other.json.rejected"]


def test_a_rejected_payload_that_cannot_be_cleared_warns_but_the_save_stands(tmp_path, caplog):
    out = tmp_path / "answers.json"
    (tmp_path / "answers.json.rejected").mkdir()  # unlink() of a directory fails on every OS

    with caplog.at_level("WARNING", logger="dokimi.artifacts"):
        save_artifact("answers", {"answers": [{"question_id": "q1"}]}, out)
    assert json.loads(out.read_text(encoding="utf-8"))["answers"] == [{"question_id": "q1"}]
    assert "answers.json.rejected" in caplog.text


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows without symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_a_save_through_a_symlink_writes_the_target_and_keeps_the_link(tmp_path):
    real_dir = tmp_path / "store"
    real_dir.mkdir()
    target = real_dir / "answers.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "answers.json"
    _symlink_or_skip(link, target)

    save_artifact("answers", {"answers": [{"question_id": "q1"}]}, link)
    assert link.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8"))["answers"] == [{"question_id": "q1"}]
    assert [p.name for p in real_dir.iterdir()] == ["answers.json"]  # no tmp left beside the target


def test_a_save_through_a_dangling_symlink_creates_the_target(tmp_path):
    target = tmp_path / "store" / "answers.json"
    target.parent.mkdir()
    link = tmp_path / "answers.json"
    _symlink_or_skip(link, target)

    save_artifact("answers", {"answers": [{"question_id": "q1"}]}, link)
    assert link.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8"))["answers"] == [{"question_id": "q1"}]


def test_a_dangling_symlink_into_a_missing_directory_is_an_error(tmp_path):
    link = tmp_path / "answers.json"
    _symlink_or_skip(link, tmp_path / "gone" / "answers.json")

    with pytest.raises(FileNotFoundError, match=r"symlink .*answers\.json.* points into a missing directory"):
        save_artifact("answers", {"answers": [{"question_id": "q1"}]}, link)
    assert link.is_symlink()
    assert not (tmp_path / "gone").exists()


def test_a_symlink_loop_is_an_error_and_both_links_survive(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _symlink_or_skip(a, b)
    b.symlink_to(a)

    with pytest.raises(OSError, match="link loop"):
        save_artifact("answers", {"answers": []}, a)
    assert a.is_symlink() and b.is_symlink()


def test_an_unstamped_payload_resolves_to_the_newest_version_it_matches():
    # Curated question sets are mostly unstamped; one written for v1 must not be judged as v2.
    from dokimi.artifacts import schema_version_for

    v1 = {"questions": [{"id": "q1", "text": "Q?"}]}
    v2 = {"questions": [{"id": "q1", "question": "Q?"}]}
    assert schema_version_for("questions", v1) == "questions.v1"
    assert schema_version_for("questions", v2) == "questions.v2"
    assert schema_version_for("questions", {"questions": []}) == "questions.v2"  # both: newest
    validate_artifact("questions", v1)
    validate_artifact("questions", v2)
    # Scorecards require their stamp, so an unstamped one matches no version: current errors.
    scorecard = {k: v for k, v in _scorecard_v1().items() if k != "schema_version"}
    assert schema_version_for("subject_scorecard", scorecard) == "subject_scorecard.v2"


def test_an_unstamped_payload_matching_no_version_reports_the_current_schemas_errors():
    from dokimi.artifacts import schema_version_for

    bad = {"questions": [{"id": "q1"}]}
    assert schema_version_for("questions", bad) == SCHEMA_VERSIONS["questions"]
    with pytest.raises(ArtifactValidationError, match="'question' is a required property"):
        validate_artifact("questions", bad)


def test_a_declared_version_is_honoured_even_when_another_would_match():
    # A file stamped v1 whose records use `question` (what `questions refine` wrote before v2)
    # is invalid as what it says it is; it is not silently re-read as v2.
    with pytest.raises(ArtifactValidationError, match="'text' is a required property"):
        validate_artifact("questions", {"schema_version": "questions.v1",
                                        "questions": [{"id": "q1", "question": "Q?"}]})


def test_an_unstamped_payload_of_a_kind_with_one_version_uses_it():
    from dokimi.artifacts import schema_version_for

    assert schema_version_for("answers", {"answers": []}) == SCHEMA_VERSIONS["answers"]
    validate_artifact("answers", {"answers": [{"question_id": "q1"}]})
