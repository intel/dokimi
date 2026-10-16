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

    pipeline = EvaluationPipeline(
        product="oneTBB",
        output_dir=output_dir,
        description="Threading Building Blocks",
        questions_from=source_dir,
    )
    result = pipeline.run(concurrency=1)

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
