# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Question-set reproducibility tests for the evaluation pipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from dokimi.orchestrator.pipeline import (
    EvaluationPipeline,
    compute_question_set_hash,
    load_questions_payload,
)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _stub_expensive_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_generate_questions(self, personas):
        raise AssertionError("question generation should be skipped")

    def generate_answers(self, questions, concurrency=5, question_set_hash=None):
        assert question_set_hash == compute_question_set_hash(questions)
        return [
            {
                "question_id": q["id"],
                "question_text": q["question"],
                "with_docs": {"answer": "context"},
                "without_docs": {"answer": "baseline"},
            }
            for q in questions
        ]

    def evaluate_answers(self, answers, concurrency=5, question_set_hash=None):
        data = {
            "run_metadata": {"question_set_hash": question_set_hash},
            "evaluations": [],
        }
        self.eval_path.write_text(json.dumps(data), encoding="utf-8")
        return []

    def generate_report(self, evaluations, questions):
        self.report_path.write_text("report", encoding="utf-8")
        return "report"

    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", no_generate_questions)
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", evaluate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", generate_report)


def test_cached_questions_skip_generation_and_get_question_set_id(tmp_path, monkeypatch):
    """The cache is this run's own artifact, so it is rewritten (as questions.v2) with the id."""
    _stub_expensive_steps(monkeypatch)
    questions = [
        {"id": "q2", "question": "How do I sort in parallel?", "source_type": "generated"},
        {"id": "q1", "question": "How do I configure workers?", "source_type": "manual"},
    ]
    output_dir = tmp_path / "run"
    _write_json(output_dir / "personas" / "oneTBB.json", {"personas": []})
    _write_json(output_dir / "questions" / "oneTBB.json", {"questions": questions})

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=output_dir,
        description="Threading Building Blocks",
    )
    result = pipeline.run(concurrency=1)

    expected_hash = compute_question_set_hash(questions)
    saved = json.loads((output_dir / "questions" / "oneTBB.json").read_text())
    assert result["question_set_hash"] == expected_hash
    assert saved["question_set_hash"] == expected_hash
    assert saved["question_set_id"] == expected_hash
    assert saved["questions"] == questions
    assert result["steps"]["questions_generated"]["cached"] is True


def test_questions_from_directory_is_reused_and_normalized(tmp_path, monkeypatch):
    _stub_expensive_steps(monkeypatch)
    questions = [
        {"id": "q1", "question": "What does task_group do?", "source_type": "generated"}
    ]
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "target"
    _write_json(source_dir / "questions" / "oneTBB.json", questions)
    _write_json(output_dir / "personas" / "oneTBB.json", {"personas": []})
    source_bytes = (source_dir / "questions" / "oneTBB.json").read_bytes()

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=output_dir,
        description="Threading Building Blocks",
        questions_from=source_dir,
    )
    result = pipeline.run(concurrency=1)

    # The id is stamped on this run's copy; the source set is read, never written.
    assert (source_dir / "questions" / "oneTBB.json").read_bytes() == source_bytes
    expected_hash = compute_question_set_hash(questions)
    saved = json.loads((output_dir / "questions" / "oneTBB.json").read_text())
    assert result["question_set_hash"] == expected_hash
    assert saved["question_set_hash"] == expected_hash
    assert saved["question_set_id"] == expected_hash
    assert saved["questions"] == questions
    assert Path(result["steps"]["questions_generated"]["external_source"]).as_posix().endswith(
        "source/questions/oneTBB.json"
    )


def test_questions_from_directory_uses_product_key_when_provided(tmp_path, monkeypatch):
    _stub_expensive_steps(monkeypatch)
    questions = [
        {"id": "q1", "question": "What does task_group do?", "source_type": "generated"}
    ]
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "target"
    _write_json(source_dir / "questions" / "onetbb.json", questions)
    _write_json(output_dir / "personas" / "onetbb.json", {"personas": []})

    pipeline = EvaluationPipeline(
        product="oneTBB",
        product_key="onetbb",
        output_dir=output_dir,
        description="Threading Building Blocks",
        questions_from=source_dir,
    )
    result = pipeline.run(concurrency=1)

    saved = json.loads((output_dir / "questions" / "onetbb.json").read_text())
    assert saved["questions"] == questions
    assert Path(result["steps"]["questions_generated"]["external_source"]).as_posix().endswith(
        "source/questions/onetbb.json"
    )


def test_auto_golden_discovery_uses_product_key(tmp_path):
    """The golden set is looked up as <product_key>_golden.json under the root."""
    questions_root = tmp_path / "questions"
    _write_json(questions_root / "demo_golden.json", {"questions": []})
    # A set named after the display name must not be picked up instead.
    _write_json(questions_root / "demoproduct_golden.json", {"questions": []})

    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=tmp_path / "out",
        description="A demo product",
        questions_root=questions_root,
    )

    assert pipeline.custom_questions_path is not None
    assert pipeline.custom_questions_path.name == "demo_golden.json"
    assert pipeline.custom_questions_path.parent == questions_root


def test_missing_golden_set_under_questions_root_warns(tmp_path, caplog):
    questions_root = tmp_path / "questions"
    questions_root.mkdir()

    with caplog.at_level(logging.WARNING):
        pipeline = EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=tmp_path / "out",
            description="A demo product",
            questions_root=questions_root,
        )

    assert pipeline.custom_questions_path is None
    assert "has no golden set for demo" in caplog.text


def test_no_questions_root_means_no_golden_set(tmp_path, caplog):
    """Without a root (and without --custom-questions) nothing is auto-included.

    The run then generates its questions, which silently makes two runs
    incomparable, so it must say so and name the flag.
    """
    with caplog.at_level(logging.WARNING):
        pipeline = EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=tmp_path / "out",
            description="A demo product",
        )

    assert pipeline.custom_questions_path is None
    assert pipeline.questions_root is None
    assert "no questions root given" in caplog.text
    assert "--questions-root" in caplog.text
    # The root-exists-but-lacks-the-set message belongs to the other branch.
    assert "has no golden set" not in caplog.text


def test_custom_questions_path_wins_over_questions_root(tmp_path):
    questions_root = tmp_path / "questions"
    _write_json(questions_root / "demo_golden.json", {"questions": []})
    explicit = tmp_path / "explicit.json"
    _write_json(explicit, {"questions": []})

    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=tmp_path / "out",
        description="A demo product",
        custom_questions_path=explicit,
        questions_root=questions_root,
    )

    assert pipeline.custom_questions_path == explicit


def test_evaluate_command_forwards_questions_root(tmp_path, monkeypatch):
    """`evaluate --questions-root` reaches EvaluationPipeline."""
    from dokimi.cli import build_parser

    seen: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, concurrency=5):
            return {
                "steps": {
                    "personas": {"path": "p"},
                    "questions_merged": {"path": "q"},
                    "answers": {"path": "a"},
                    "evaluation": {"path": "e"},
                    "report": {"path": "r"},
                }
            }

    monkeypatch.setattr(
        "dokimi.orchestrator.EvaluationPipeline", FakePipeline
    )
    questions_root = tmp_path / "questions"
    questions_root.mkdir()

    args = build_parser().parse_args([
        "evaluate",
        "--product", "DemoProduct",
        "--description", "A demo product",
        "--output-dir", str(tmp_path / "out"),
        "--questions-root", str(questions_root),
    ])
    args.func(args)

    assert seen["questions_root"] == questions_root


def test_load_questions_payload_rejects_invalid_wrapped_payload(tmp_path):
    path = tmp_path / "questions.json"
    _write_json(path, {"questions": {"q1": "not a list"}})

    with pytest.raises(ValueError, match="Expected a list of questions"):
        load_questions_payload(path)


def test_questions_from_wins_over_cache(tmp_path, monkeypatch):
    """When both --questions-from and cache are present, --questions-from is used."""
    _stub_expensive_steps(monkeypatch)
    external_questions = [
        {"id": "external", "question": "From external source?", "source_type": "generated"}
    ]
    cached_questions = [
        {"id": "cached", "question": "From cache?", "source_type": "generated"}
    ]
    external_dir = tmp_path / "external"
    output_dir = tmp_path / "target"
    _write_json(external_dir / "questions" / "oneTBB.json", external_questions)
    _write_json(output_dir / "personas" / "oneTBB.json", {"personas": []})
    _write_json(output_dir / "questions" / "oneTBB.json", cached_questions)

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=output_dir,
        description="Threading Building Blocks",
        questions_from=external_dir,
    )
    pipeline.run(concurrency=1)

    # Verify external set was used, not cache
    saved = json.loads((output_dir / "questions" / "oneTBB.json").read_text())
    assert saved["questions"][0]["id"] == "external"
    assert len(saved["questions"]) == 1


def test_relative_questions_from_path_not_confused_with_output(tmp_path, monkeypatch):
    """A relative input path resolves correctly even when input and output paths match."""
    _stub_expensive_steps(monkeypatch)
    import os

    questions = [
        {"id": "q1", "question": "What is this?", "source_type": "generated"}
    ]
    # Create input and output in same directory with relative paths
    shared_dir = tmp_path / "shared"
    _write_json(shared_dir / "questions" / "oneTBB.json", questions)
    _write_json(shared_dir / "personas" / "oneTBB.json", {"personas": []})

    input_bytes_before = (shared_dir / "questions" / "oneTBB.json").read_bytes()

    # Change to tmp_path to use relative paths
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        (tmp_path / "sub").mkdir()
        # The same directory, spelled two ways: only a resolved comparison sees one path
        pipeline = EvaluationPipeline(
            product="oneTBB",
            output_dir=Path("shared"),
            description="Threading Building Blocks",
            questions_from=Path("sub/../shared"),
        )
        pipeline.run(concurrency=1)

        # External input files (--questions-from) are never written, even if they're the output path
        input_bytes_after = Path("shared/questions/oneTBB.json").read_bytes()
        assert input_bytes_after == input_bytes_before, \
            "input file should not be modified by --questions-from even when it is the output path"
    finally:
        os.chdir(old_cwd)


def test_same_path_force_regen_merge_persists_merged_set(tmp_path, monkeypatch):
    """When --force-regen merges into the run's own questions file, the merged set is saved."""
    # Create custom file at the output location
    custom_file = tmp_path / "questions" / "oneTBB.json"
    custom_questions = [{"id": "c1", "question": "Custom question?"}]
    _write_json(custom_file, custom_questions)
    _write_json(tmp_path / "personas" / "oneTBB.json", {"personas": []})

    def stub_discover(self):
        return {"personas": []}

    def stub_generate(self, personas):
        # Return one generated question to merge with the custom one
        return [{"id": "g1", "question": "Generated question?", "source_type": "generated"}]

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", stub_discover)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", stub_generate)

    # Stub expensive steps
    def generate_answers(self, questions, concurrency=5, question_set_hash=None):
        return [
            {
                "question_id": q["id"],
                "question_text": q["question"],
                "with_docs": {"answer": "context"},
                "without_docs": {"answer": "baseline"},
            }
            for q in questions
        ]

    def evaluate_answers(self, answers, concurrency=5, question_set_hash=None):
        data = {
            "run_metadata": {"question_set_hash": question_set_hash},
            "evaluations": [],
        }
        self.eval_path.write_text(json.dumps(data), encoding="utf-8")
        return []

    def generate_report(self, evaluations, questions):
        self.report_path.write_text("report", encoding="utf-8")
        return "report"

    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", evaluate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", generate_report)

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
        custom_questions_path=custom_file,
        force_regen=True,
    )
    pipeline.run(concurrency=1)

    # The merged set (g1 + c1) should be saved with question_set_id
    saved = json.loads(custom_file.read_text())
    assert len(saved["questions"]) == 2
    ids = {q["id"] for q in saved["questions"]}
    assert ids == {"g1", "c1"}
    assert "question_set_id" in saved


def test_generation_returning_empty_question_fails_before_answers(tmp_path, monkeypatch):
    """An empty question in merged set fails before any answer is generated."""
    def stub_discover(self):
        return {"personas": []}

    def stub_generate(self, personas):
        # Return a generated question with empty text
        return [{"id": "g1", "question": "", "source_type": "generated"}]

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", stub_discover)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", stub_generate)

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
    )

    # Should raise ArtifactValidationError before generating answers
    with pytest.raises(Exception, match="should be non-empty"):
        pipeline.run(concurrency=1)


def test_cached_resume_with_custom_input_at_same_path_leaves_input_unchanged(tmp_path, monkeypatch):
    """When output path == custom input path on cached resume, the input is not rewritten."""
    _stub_expensive_steps(monkeypatch)

    # Create cached file at the output location
    cache_file = tmp_path / "questions" / "oneTBB.json"
    questions = [{"id": "q1", "question": "Cached question?"}]
    _write_json(cache_file, questions)
    cache_bytes_before = cache_file.read_bytes()
    _write_json(tmp_path / "personas" / "oneTBB.json", {"personas": []})

    # Use the cache file as custom_questions_path (same location)
    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
        custom_questions_path=cache_file,
    )
    pipeline.run(concurrency=1)

    # The input file must not be rewritten (same bytes)
    cache_bytes_after = cache_file.read_bytes()
    assert cache_bytes_after == cache_bytes_before
