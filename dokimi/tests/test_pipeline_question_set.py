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


@pytest.mark.outcome("pinned-questions-from")
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


@pytest.mark.outcome("pinned-questions-warn")
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
    # Verify source_types are correct: g1 is generated, c1 is manual
    questions_by_id = {q["id"]: q for q in saved["questions"]}
    assert questions_by_id["g1"].get("source_type") == "generated"
    assert questions_by_id["c1"].get("source_type") == "manual"


def test_same_path_force_regen_preserves_source_types(tmp_path, monkeypatch):
    """Generated questions keep source_type: generated when reloaded from same-path write."""
    # Run 1: Generate g1, merge with c1, save to same-path file
    custom_file = tmp_path / "questions" / "oneTBB.json"
    custom_questions = [{"id": "c1", "question": "Custom question?"}]
    _write_json(custom_file, custom_questions)
    _write_json(tmp_path / "personas" / "oneTBB.json", {"personas": []})

    def stub_discover(self):
        return {"personas": []}

    call_count = [0]
    def stub_generate(self, personas):
        # Run 1: generate g1
        call_count[0] += 1
        if call_count[0] == 1:
            return [{"id": "g1", "question": "Generated question?", "source_type": "generated"}]
        # Run 2: generate g2
        return [{"id": "g2", "question": "Generated question 2?", "source_type": "generated"}]

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

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", stub_discover)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", stub_generate)
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", evaluate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", generate_report)

    # Run 1: force_regen with custom input at same path
    pipeline1 = EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
        custom_questions_path=custom_file,
        force_regen=True,
    )
    pipeline1.run(concurrency=1)

    saved1 = json.loads(custom_file.read_text())
    assert {q["id"] for q in saved1["questions"]} == {"g1", "c1"}
    assert {q.get("source_type") for q in saved1["questions"]} == {"generated", "manual"}

    # Run 2: force_regen again; g1 should still be "generated", not downgraded to "manual"
    pipeline2 = EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
        custom_questions_path=custom_file,
        force_regen=True,
    )
    pipeline2.run(concurrency=1)

    saved2 = json.loads(custom_file.read_text())
    # Now we have g2, g1, c1 (g2 is generated, g1 kept "generated", c1 is "manual")
    questions_by_id = {q["id"]: q for q in saved2["questions"]}
    assert questions_by_id["g1"].get("source_type") == "generated", \
        "g1 from Run 1 should keep source_type: generated, not be downgraded to manual"
    assert questions_by_id["g2"].get("source_type") == "generated"
    assert questions_by_id["c1"].get("source_type") == "manual"


def test_same_path_force_regen_with_empty_generation_leaves_input_unchanged(tmp_path, monkeypatch):
    """When generation returns [], custom input at same path is not written."""
    custom_file = tmp_path / "questions" / "oneTBB.json"
    custom_questions = [{"id": "c1", "question": "Custom v1?"}]
    _write_json(custom_file, custom_questions)
    custom_bytes_before = custom_file.read_bytes()
    _write_json(tmp_path / "personas" / "oneTBB.json", {"personas": []})

    def stub_discover(self):
        return {"personas": []}

    def stub_generate(self, personas):
        # Generation returns nothing
        return []

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

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", stub_discover)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", stub_generate)
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

    # Input file must not be rewritten (no generated questions to merge)
    custom_bytes_after = custom_file.read_bytes()
    assert custom_bytes_after == custom_bytes_before


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


def test_same_path_force_regen_with_deduped_generation_leaves_input_unchanged(tmp_path, monkeypatch):
    """When dedup drops every generated question, custom input at same path is not written."""
    custom_file = tmp_path / "questions" / "oneTBB.json"
    custom_questions = [{"id": "c1", "question": "Custom v1?"}]
    _write_json(custom_file, custom_questions)
    custom_bytes_before = custom_file.read_bytes()
    _write_json(tmp_path / "personas" / "oneTBB.json", {"personas": []})

    def stub_discover(self):
        return {"personas": []}

    def stub_generate(self, personas):
        return [{"id": "g1", "question": "Custom v1 paraphrase?", "source_type": "generated"}]

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

    monkeypatch.setattr(
        "dokimi.questions.QuestionValidator._deduplicate",
        lambda self, qs, protect=None: (
            [q for q in qs if q.get("source_type") == "manual"], [[q["id"] for q in qs]]
        ),
    )
    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", stub_discover)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", stub_generate)
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

    # Input file must not be rewritten (no generated questions to merge)
    custom_bytes_after = custom_file.read_bytes()
    assert custom_bytes_after == custom_bytes_before


def _stub_regen_steps(monkeypatch: pytest.MonkeyPatch, generated: list[dict]) -> None:
    """A --force-regen run whose generation returns ``generated``; answers and judging stubbed."""
    _stub_expensive_steps(monkeypatch)
    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", lambda self: {"personas": []})
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions",
                        lambda self, personas: [dict(q) for q in generated])


def _own_output_pipeline(tmp_path: Path) -> EvaluationPipeline:
    return EvaluationPipeline(
        product="oneTBB",
        output_dir=tmp_path,
        description="Threading Building Blocks",
        custom_questions_path=tmp_path / "questions" / "oneTBB.json",
        force_regen=True,
    )


def test_a_stubbed_merge_that_returns_the_custom_set_does_not_write_it(tmp_path, monkeypatch):
    """No merge-side signal is needed: the run compares ids with what it read, so it fails closed."""
    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "c1", "text": "Custom v1?"}])
    before = custom_file.read_bytes()
    _stub_regen_steps(monkeypatch, [{"id": "g1", "question": "Generated?"}])
    monkeypatch.setattr(EvaluationPipeline, "_merge_questions",
                        lambda self, generated: [{"id": "c1", "question": "Custom v1?"}])

    _own_output_pipeline(tmp_path).run(concurrency=1)

    assert custom_file.read_bytes() == before


def test_same_path_force_regen_validates_the_merged_set_before_any_answer(tmp_path, monkeypatch):
    from dokimi.artifacts import ArtifactValidationError

    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "c1", "text": "Custom v1?"}])
    before = custom_file.read_bytes()
    _stub_regen_steps(monkeypatch, [{"id": "g1", "question": "", "source_type": "generated"}])

    def no_answers(self, *a, **kw):
        raise AssertionError("answers were generated for an invalid set")

    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", no_answers)
    with pytest.raises(ArtifactValidationError, match="should be non-empty"):
        _own_output_pipeline(tmp_path).run(concurrency=1)
    assert custom_file.read_bytes() == before


def test_a_custom_set_elsewhere_is_still_tagged_manual(tmp_path, monkeypatch):
    """Only the run's own file keeps its labels; any other custom set is curated, as on main."""
    custom_file = tmp_path / "golden.json"
    _write_json(custom_file, [{"id": "c1", "question": "Custom?", "source_type": "generated"}])
    _stub_regen_steps(monkeypatch, [])

    pipeline = EvaluationPipeline(product="oneTBB", output_dir=tmp_path / "run",
                                  description="Threading Building Blocks",
                                  custom_questions_path=custom_file, force_regen=True)
    pipeline.run(concurrency=1)

    saved = json.loads((tmp_path / "run" / "questions" / "oneTBB.json").read_text())
    assert [q["source_type"] for q in saved["questions"]] == ["manual"]


def test_a_rerun_that_adds_nothing_leaves_the_previous_runs_merge_unchanged(tmp_path, monkeypatch):
    """Run 1 saves g1 + c1 over its own file; run 2 generates nothing, so the file stays as is.

    The file now holds a generated record, so "the set has a generated record" is not the
    signal: only a record the merge added to what was read is.
    """
    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "c1", "text": "Custom v1?"}])
    _stub_regen_steps(monkeypatch, [{"id": "g1", "question": "Generated one?",
                                     "source_type": "generated"}])
    _own_output_pipeline(tmp_path).run(concurrency=1)
    after_first = custom_file.read_bytes()
    assert {q["id"] for q in json.loads(after_first)["questions"]} == {"g1", "c1"}

    _stub_regen_steps(monkeypatch, [])
    _own_output_pipeline(tmp_path).run(concurrency=1)

    assert custom_file.read_bytes() == after_first


@pytest.mark.outcome("pinned-questions-root")
def test_two_runs_on_one_questions_root_answer_the_same_questions(tmp_path, monkeypatch):
    """Runs into fresh output dirs that share a root answer exactly the golden set.

    Generation is stubbed to return different ids on each call, so a run that
    still generated (and merged) would hand a different set to the answer step.
    """
    golden = [
        {"id": "g1", "question": "How do I cancel a task_group?"},
        {"id": "g2", "question": "When does parallel_for split a range?"},
    ]
    questions_root = tmp_path / "content" / "questions"
    _write_json(questions_root / "demo_golden.json", {"questions": golden})

    calls = iter(range(100))
    answered: list[list[str]] = []

    def generate_questions(self, personas):
        n = next(calls)
        return [{"id": f"gen{n}", "question": f"Generated question {n}?", "source_type": "generated"}]

    def generate_answers(self, questions, concurrency=5, question_set_hash=None):
        answered.append([q["id"] for q in questions])
        return []

    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", generate_questions)
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", lambda self, a, concurrency=5, question_set_hash=None: [])
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", lambda self, e, q: "report")

    for run in ("run_a", "run_b"):
        output_dir = tmp_path / run
        _write_json(output_dir / "personas" / "demo.json", {"personas": []})
        EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=output_dir,
            description="A demo product",
            questions_root=questions_root,
        ).run(concurrency=1)

    assert answered == [["g1", "g2"], ["g1", "g2"]]


@pytest.mark.outcome("pinned-questions-root")
def test_empty_golden_set_fails_the_run_and_writes_no_cache(tmp_path, monkeypatch):
    questions_root = tmp_path / "questions"
    _write_json(questions_root / "demo_golden.json", {"questions": []})
    output_dir = tmp_path / "out"
    _write_json(output_dir / "personas" / "demo.json", {"personas": []})
    _stub_expensive_steps(monkeypatch)
    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=output_dir,
        description="A demo product",
        questions_root=questions_root,
    )

    with pytest.raises(ValueError, match="Golden set is empty"):
        pipeline.run(concurrency=1)

    assert not pipeline.questions_path.exists()


@pytest.mark.outcome("pinned-questions-root")
def test_cached_set_wins_over_questions_root_until_force_regen(tmp_path, monkeypatch):
    questions_root = tmp_path / "questions"
    _write_json(questions_root / "demo_golden.json", {"questions": [{"id": "g1", "question": "Golden?"}]})
    output_dir = tmp_path / "out"
    _write_json(output_dir / "personas" / "demo.json", {"personas": []})
    _write_json(output_dir / "questions" / "demo.json", {"questions": [{"id": "old", "question": "Cached?"}]})
    answered: list[list[str]] = []

    def generate_answers(self, questions, concurrency=5, question_set_hash=None):
        answered.append([q["id"] for q in questions])
        return []

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", lambda self: {"personas": []})
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", lambda self, a, concurrency=5, question_set_hash=None: [])
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", lambda self, e, q: "report")

    for force_regen in (False, True):
        EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=output_dir,
            description="A demo product",
            questions_root=questions_root,
            force_regen=force_regen,
        ).run(concurrency=1)

    assert answered == [["old"], ["g1"]]


@pytest.mark.outcome("pinned-questions-warn")
def test_questions_from_does_not_warn_that_questions_are_generated(tmp_path, caplog):
    source_dir = tmp_path / "source"
    _write_json(source_dir / "questions" / "demo.json", [{"id": "q1", "question": "Q?"}])

    with caplog.at_level(logging.WARNING):
        EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=tmp_path / "out",
            description="A demo product",
            questions_from=source_dir,
        )

    assert "questions will be generated" not in caplog.text


@pytest.mark.outcome("pinned-questions-warn")
def test_cached_questions_differing_from_golden_warns(tmp_path, monkeypatch, caplog):
    """When cached question ids differ from golden ids, a warning is logged."""
    _stub_expensive_steps(monkeypatch)
    questions_root = tmp_path / "questions"
    output_dir = tmp_path / "out"
    # Golden set with different ids
    _write_json(questions_root / "demo_golden.json", [
        {"id": "g1", "question": "Golden 1?"},
        {"id": "g2", "question": "Golden 2?"}
    ])
    # Cached set with different ids
    _write_json(output_dir / "questions" / "demo.json", [
        {"id": "old1", "question": "Old 1?"},
        {"id": "old2", "question": "Old 2?"}
    ])

    with caplog.at_level(logging.WARNING):
        pipeline = EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=output_dir,
            description="A demo product",
            questions_root=questions_root,
        )
        pipeline.run(concurrency=1)

    assert "cached question set" in caplog.text
    assert "hash=" in caplog.text
    assert "differs from golden set" in caplog.text


@pytest.mark.outcome("pinned-questions-reuse")
def test_golden_pinned_run_skips_persona_discovery(tmp_path, monkeypatch):
    """With a golden pinned root, persona discovery should not run even if no cache."""
    _stub_expensive_steps(monkeypatch)
    questions_root = tmp_path / "questions"
    output_dir = tmp_path / "out"
    _write_json(questions_root / "demo_golden.json", [{"id": "g1", "question": "Q?"}])

    discover_personas_called = []

    def track_discover_personas(self):
        discover_personas_called.append(True)
        raise AssertionError("persona discovery should be skipped for golden pinned")

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", track_discover_personas)

    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=output_dir,
        description="A demo product",
        questions_root=questions_root,
    )
    result = pipeline.run(concurrency=1)

    assert not discover_personas_called, "persona discovery should not have been called"
    assert result["steps"]["personas"]["skipped"] is True
    # When skipped, path is not set; orchestrate.py checks skipped flag before printing
    assert "path" not in result["steps"]["personas"], "path should not be set when skipped"


@pytest.mark.outcome("pinned-questions-reuse")
def test_questions_from_run_skips_persona_discovery(tmp_path, monkeypatch):
    """With --questions-from, persona discovery should not run."""
    _stub_expensive_steps(monkeypatch)
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "out"
    _write_json(source_dir / "questions" / "demo.json", [{"id": "q1", "question": "Q?"}])

    discover_personas_called = []

    def track_discover_personas(self):
        discover_personas_called.append(True)
        raise AssertionError("persona discovery should be skipped for external questions")

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", track_discover_personas)

    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=output_dir,
        description="A demo product",
        questions_from=source_dir,
    )
    result = pipeline.run(concurrency=1)

    assert not discover_personas_called, "persona discovery should not have been called"
    assert result["steps"]["personas"]["skipped"] is True


def test_questions_from_with_root_ignores_root(tmp_path, caplog):
    """When --questions-from is set with a --questions-root, the root is ignored."""
    source_dir = tmp_path / "source"
    questions_root = tmp_path / "questions"
    output_dir = tmp_path / "out"
    _write_json(source_dir / "questions" / "demo.json", [{"id": "q1", "question": "Q?"}])
    _write_json(questions_root / "demo_golden.json", [{"id": "g1", "question": "Golden?"}])

    with caplog.at_level(logging.WARNING):
        pipeline = EvaluationPipeline(
            product="DemoProduct",
            product_key="demo",
            output_dir=output_dir,
            description="A demo product",
            questions_from=source_dir,
            questions_root=questions_root,
        )

    # golden_pinned should be False and external_questions_path should be set
    assert pipeline.golden_pinned is False
    assert pipeline.external_questions_path is not None
    # Should not log "Using golden questions"
    assert "Using golden questions" not in caplog.text


def test_custom_questions_path_with_root(tmp_path, monkeypatch):
    """With explicit --custom-questions and a root, custom merges into generated; golden_pinned stays False."""
    questions_root = tmp_path / "questions"
    custom_q = tmp_path / "custom.json"
    output_dir = tmp_path / "out"
    _write_json(questions_root / "demo_golden.json", [{"id": "g1", "question": "Golden?"}])
    _write_json(custom_q, [{"id": "c1", "question": "Custom?"}])

    # Stub all expensive steps
    def discover_personas(self):
        return {"personas": []}

    def generate_questions(self, personas):
        return [{"id": "gen1", "question": "Generated?", "source_type": "generated"}]

    def generate_answers(self, questions, concurrency=5, question_set_hash=None):
        return []

    def evaluate_answers(self, answers, concurrency=5, question_set_hash=None):
        data = {"run_metadata": {"question_set_hash": question_set_hash}, "evaluations": []}
        self.eval_path.write_text(json.dumps(data), encoding="utf-8")
        return []

    def generate_report(self, evaluations, questions):
        self.report_path.write_text("report", encoding="utf-8")
        return "report"

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", discover_personas)
    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", generate_questions)
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", generate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", evaluate_answers)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", generate_report)

    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=output_dir,
        description="A demo product",
        custom_questions_path=custom_q,
        questions_root=questions_root,
    )

    # custom_questions_path is set, but golden_pinned stays False (explicit custom != auto-golden)
    assert pipeline.custom_questions_path == custom_q
    assert pipeline.golden_pinned is False

    # Run should merge custom into generated questions (generation still runs)
    pipeline.run(concurrency=1)
    saved = json.loads((output_dir / "questions" / "demo.json").read_text())
    # Custom and generated questions should both be present
    question_ids = {q["id"] for q in saved["questions"]}
    assert "c1" in question_ids, "custom question should be in result"
    assert "gen1" in question_ids, "generated question should be in result"
    assert "g1" not in question_ids, "golden-only question should not be in result"


def _golden_run(tmp_path, golden, cached, *, raw=False, custom=False):
    root = tmp_path / "questions"
    out = tmp_path / "out"
    if raw:
        root.mkdir(parents=True)
        (root / "demo_golden.json").write_text(golden, encoding="utf-8")
    else:
        _write_json(root / "demo_golden.json", golden)
    if cached is not None:
        _write_json(out / "questions" / "demo.json", cached)
    kwargs = {"custom_questions_path": root / "demo_golden.json"} if custom else {"questions_root": root}
    return EvaluationPipeline(
        product="DemoProduct", product_key="demo", output_dir=out,
        description="A demo product", **kwargs,
    ).run(concurrency=1)


@pytest.mark.parametrize("golden,raw", [(["just a string"], False), ('{"questions": [', True)])
def test_cached_run_tolerates_unreadable_golden_file(tmp_path, monkeypatch, caplog, golden, raw):
    _stub_expensive_steps(monkeypatch)
    with caplog.at_level(logging.WARNING):
        _golden_run(tmp_path, golden, [{"id": "a", "question": "A?"}], raw=raw)
    assert "Could not load golden set" in caplog.text


def test_pinned_run_without_cache_still_raises_on_corrupt_golden(tmp_path, monkeypatch):
    _stub_expensive_steps(monkeypatch)
    with pytest.raises(json.JSONDecodeError):
        _golden_run(tmp_path, '{"questions": [', None, raw=True)


def test_cached_same_ids_edited_text_warns(tmp_path, monkeypatch, caplog):
    _stub_expensive_steps(monkeypatch)
    with caplog.at_level(logging.WARNING):
        _golden_run(tmp_path, [{"id": "g1", "question": "NEW?"}], [{"id": "g1", "question": "OLD?"}])
    assert "differs from golden set" in caplog.text


def test_cached_equal_to_golden_does_not_warn(tmp_path, monkeypatch, caplog):
    _stub_expensive_steps(monkeypatch)
    qs = [{"id": "g1", "question": "Same?"}, {"id": "g2", "question": "Also?"}]
    with caplog.at_level(logging.WARNING):
        _golden_run(tmp_path, qs, [dict(q) for q in qs])
    assert "differs from golden set" not in caplog.text
    assert "Could not load golden set" not in caplog.text


def test_custom_questions_with_merged_cache_does_not_warn(tmp_path, monkeypatch, caplog):
    _stub_expensive_steps(monkeypatch)
    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", lambda self: {"personas": []})
    cached = [{"id": "g1", "question": "Golden?"}, {"id": "x", "question": "Generated?"}]
    with caplog.at_level(logging.WARNING):
        _golden_run(tmp_path, [{"id": "g1", "question": "Golden?"}], cached, custom=True)
    assert "differs from golden set" not in caplog.text


def test_evaluate_prints_no_personas_line_when_personas_skipped(tmp_path, monkeypatch, capsys):
    from dokimi.cli import build_parser

    def run(self, concurrency=5):
        return {"steps": {
            "personas": {"count": 0, "skipped": True},
            "questions_merged": {"path": "q"},
            "answers": {"path": "a"},
            "evaluation": {"path": "e"},
            "report": {"path": "r"},
        }}

    monkeypatch.setattr(EvaluationPipeline, "run", run)
    args = build_parser().parse_args([
        "evaluate", "--product", "DemoProduct", "--description", "d",
        "--output-dir", str(tmp_path / "out"),
    ])
    args.func(args)
    out = capsys.readouterr().out
    assert "Questions:" in out
    assert "Personas:" not in out


@pytest.mark.parametrize("exc", [OSError("permission denied"), TypeError("bad item")])
def test_cached_run_tolerates_golden_read_errors(tmp_path, monkeypatch, caplog, exc):
    import dokimi.orchestrator.pipeline as pl

    _stub_expensive_steps(monkeypatch)
    real = pl.load_questions_payload

    def flaky(path):
        if path.name.endswith("_golden.json"):
            raise exc
        return real(path)

    monkeypatch.setattr(pl, "load_questions_payload", flaky)
    with caplog.at_level(logging.WARNING):
        _golden_run(tmp_path, [{"id": "g", "question": "G?"}], [{"id": "a", "question": "A?"}])
    assert "Could not load golden set" in caplog.text



def test_a_regeneration_under_a_colliding_id_is_saved(tmp_path, monkeypatch):
    """Generated ids restart at q_001; a new text under an old id is a different set, so it is saved."""
    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "q_001", "question": "Old generated one?",
                               "source_type": "generated"}])
    _stub_regen_steps(monkeypatch, [{"id": "q_001", "question": "Brand new generated one?",
                                     "source_type": "generated"}])
    _own_output_pipeline(tmp_path).run(concurrency=1)

    saved = json.loads(custom_file.read_text())
    assert sorted(q["question"] for q in saved["questions"]) == [
        "Brand new generated one?", "Old generated one?"]


def test_without_a_read_custom_set_the_own_file_is_not_written(tmp_path, monkeypatch):
    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "c1", "question": "Custom?"}])
    before = custom_file.read_bytes()
    _stub_regen_steps(monkeypatch, [])
    monkeypatch.setattr(EvaluationPipeline, "_read_question_inputs", lambda self: None)
    _own_output_pipeline(tmp_path).run(concurrency=1)
    assert custom_file.read_bytes() == before


def test_generated_records_of_the_own_file_get_no_manual_persona(tmp_path, monkeypatch):
    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "c1", "question": "Custom?"}])
    _stub_regen_steps(monkeypatch, [{"id": "g1", "question": "Generated one?",
                                     "source_type": "generated"}])
    _own_output_pipeline(tmp_path).run(concurrency=1)
    _stub_regen_steps(monkeypatch, [{"id": "g2", "question": "Generated two?",
                                     "source_type": "generated"}])
    _own_output_pipeline(tmp_path).run(concurrency=1)

    by_id = {q["id"]: q for q in json.loads(custom_file.read_text())["questions"]}
    assert "persona_id" not in by_id["g1"]
    assert by_id["c1"]["persona_id"] == "manual"


def test_a_relative_own_output_path_keeps_source_types(tmp_path, monkeypatch):
    import os

    custom_file = tmp_path / "questions" / "oneTBB.json"
    _write_json(custom_file, [{"id": "g0", "question": "Earlier generated?",
                               "source_type": "generated"}])
    _stub_regen_steps(monkeypatch, [{"id": "g1", "question": "Generated one?",
                                     "source_type": "generated"}])
    (tmp_path / "sub").mkdir()
    old = os.getcwd()
    try:
        os.chdir(tmp_path)
        EvaluationPipeline(product="oneTBB", output_dir=Path("."),
                           description="Threading Building Blocks",
                           custom_questions_path=Path("sub/../questions/oneTBB.json"),
                           force_regen=True).run(concurrency=1)
    finally:
        os.chdir(old)
    by_id = {q["id"]: q for q in json.loads(custom_file.read_text())["questions"]}
    assert by_id["g0"]["source_type"] == "generated"
