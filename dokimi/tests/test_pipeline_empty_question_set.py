# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run whose question set is empty is refused, not ended as a success."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.commands import benchmark
from dokimi.orchestrator.pipeline import EvaluationPipeline
from dokimi.registry import ProductEntry
from tests.cli_refusal import refusal


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("source", ["cached", "questions_from", "generated"])
def test_an_empty_question_set_fails_the_run_before_any_model_call(tmp_path, monkeypatch, source):
    output_dir = tmp_path / "out"
    _write_json(output_dir / "personas" / "demo.json", {"personas": []})
    questions_from = None
    if source == "cached":
        _write_json(output_dir / "questions" / "demo.json", {"questions": []})
    elif source == "questions_from":
        questions_from = tmp_path / "source" / "demo.json"
        _write_json(questions_from, {"questions": []})

    def no_questions(self, personas):
        if source != "generated":
            raise AssertionError("questions are not generated when a set is reused")
        return []

    def no_answers(self, questions, concurrency=5, question_set_hash=None):
        raise AssertionError("no answers should be generated for an empty question set")

    monkeypatch.setattr(EvaluationPipeline, "_generate_questions", no_questions)
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", no_answers)
    pipeline = EvaluationPipeline(
        product="DemoProduct",
        product_key="demo",
        output_dir=output_dir,
        description="A demo product",
        questions_from=questions_from,
    )

    with pytest.raises(ValueError, match="No questions to answer"):
        pipeline.run(concurrency=1)

    assert not pipeline.eval_path.exists()


class _Registry:
    def get(self, key: str) -> ProductEntry:
        return ProductEntry(key=key, name="oneTBB", description="Threading Building Blocks", doc_sources=["local:docs"])


def test_an_empty_cached_question_set_is_an_input_refusal_before_any_model_call(tmp_path, monkeypatch):
    """`benchmark run` over an output directory whose cached set is empty exits 2, not 5, and discovers no personas."""
    out = tmp_path / "out"
    _write_json(out / "questions" / "onetbb.json", {"questions": []})
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    def no_personas(self):
        raise AssertionError("no personas should be discovered for an empty cached question set")

    monkeypatch.setattr(EvaluationPipeline, "_discover_personas", no_personas)
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    args = build_parser().parse_args([
        "benchmark", "run", "--library", "onetbb", "--out-dir", str(out), "--provider", "openai",
        "--judge-provider", "openai", "--judge-model", "judge-x",
    ])

    with refusal() as outcome:
        benchmark.cmd_benchmark_run(args)

    assert outcome.code == 2
    assert "Error: No questions to answer" in outcome.err
