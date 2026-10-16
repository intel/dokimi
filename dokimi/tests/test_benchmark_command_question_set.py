# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Command-level tests for fixed question-set benchmark runs."""

from __future__ import annotations

import argparse
import json

from dokimi.cli import build_parser
from dokimi.commands import benchmark
from dokimi.registry import ProductEntry


class _Registry:
    def get(self, key: str) -> ProductEntry:
        assert key == "onetbb"
        return ProductEntry(
            key="onetbb",
            name="oneTBB",
            description="Threading Building Blocks",
            doc_sources=["local:docs"],
        )


def test_multi_run_reuses_first_run_questions(monkeypatch, tmp_path, capsys):
    calls: list[dict[str, object]] = []

    def fake_run_single_library(entry, **kwargs):
        calls.append(kwargs)
        return {
            "library": entry.key,
            "name": entry.name,
            "status": "ok",
            "result": {
                "steps": {
                    "evaluation": {
                        "summary": {"with_avg": 80.0},
                    }
                }
            },
        }

    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(benchmark, "_run_single_library", fake_run_single_library)

    output_dir = tmp_path / "onetbb_eval"
    args = argparse.Namespace(
        library="onetbb",
        output_dir=str(output_dir),
        model="gpt-6.1-sol",
        provider="openai",
        judge_model="gpt-4o",
        judge_provider="openai",
        doc_source=None,
        max_tokens=4000,
        force_regen=True,
        concurrency=2,
        questions_from=None,
        multi_run=3,
    )

    benchmark.cmd_benchmark_run(args)

    assert [call["output_dir"] for call in calls] == [
        f"{output_dir}_run1",
        f"{output_dir}_run2",
        f"{output_dir}_run3",
    ]
    assert [call["questions_from"] for call in calls] == [
        None,
        f"{output_dir}_run1",
        f"{output_dir}_run1",
    ]
    assert [call["force_regen"] for call in calls] == [True, False, False]

    captured = capsys.readouterr()
    assert "Multi-run summary (3 runs)" in captured.out


def test_batch_uses_per_target_subdirs_for_custom_output(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    def fake_run_single_library(entry, **kwargs):
        calls.append({"entry": entry.key, **kwargs})
        return {"library": entry.key, "name": entry.name, "status": "ok", "result": {}}

    class Registry:
        def get(self, key: str) -> ProductEntry:
            return ProductEntry(key=key, name=key, description=key, doc_sources=[])

    monkeypatch.setattr(benchmark, "_load_registry", lambda args: Registry())
    monkeypatch.setattr(benchmark, "_run_single_library", fake_run_single_library)

    output_dir = tmp_path / "benchmarks"
    args = argparse.Namespace(
        libraries="onetbb,onedal",
        all_libraries=False,
        output_dir=str(output_dir),
        model="gpt-6.1-sol",
        provider="openai",
        judge_model="gpt-4o",
        judge_provider="openai",
        doc_source=None,
        max_tokens=4000,
        force_regen=False,
        concurrency=2,
        fail_fast=False,
    )

    benchmark.cmd_benchmark_batch(args)

    assert [call["output_dir"] for call in calls] == [
        str(output_dir / "onetbb_final"),
        str(output_dir / "onedal_final"),
    ]


def test_questions_root_is_forwarded_to_every_run(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    def fake_run_single_library(entry, **kwargs):
        calls.append(kwargs)
        return {"library": entry.key, "name": entry.name, "status": "ok", "result": {}}

    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(benchmark, "_run_single_library", fake_run_single_library)

    questions_root = tmp_path / "content-repo" / "questions"
    questions_root.mkdir(parents=True)
    args = build_parser().parse_args([
        "benchmark", "run", "--library", "onetbb",
        "--output-dir", str(tmp_path / "out"),
        "--questions-root", str(questions_root),
    ])
    assert args.questions_root == str(questions_root)

    benchmark.cmd_benchmark_run(args)
    assert [call["questions_root"] for call in calls] == [str(questions_root)]


def test_questions_root_reaches_the_pipeline(monkeypatch, tmp_path):
    """--questions-root ends up as EvaluationPipeline(questions_root=...)."""
    seen: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, concurrency=5):
            return {}

    monkeypatch.setattr(
        "dokimi.orchestrator.EvaluationPipeline", FakePipeline
    )
    questions_root = tmp_path / "questions"
    questions_root.mkdir()

    benchmark._run_single_library(
        _Registry().get("onetbb"),
        output_dir=str(tmp_path / "out"),
        model="m",
        provider="openai",
        judge_model="j",
        questions_root=str(questions_root),
    )

    assert seen["questions_root"] == questions_root


def test_preflight_reports_the_questions_root(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(
        "dokimi.mcp.factory.create_doc_source_client",
        lambda doc_source: object(),
    )
    questions_root = tmp_path / "questions"
    questions_root.mkdir()

    args = build_parser().parse_args([
        "benchmark", "preflight", "--library", "onetbb",
        "--doc-source", "local:docs",
        "--output-dir", str(tmp_path / "out"),
        "--questions-root", str(questions_root),
        "--provider", "openai", "--judge-provider", "openai",
    ])
    args.func(args)

    assert f"questions_root: {questions_root}" in capsys.readouterr().out


def test_preflight_validates_questions_from(monkeypatch, tmp_path, capsys):
    questions_dir = tmp_path / "source" / "questions"
    questions_dir.mkdir(parents=True)
    (questions_dir / "onetbb.json").write_text(
        json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8"
    )

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(
        "dokimi.mcp.factory.create_doc_source_client",
        lambda doc_source: object(),
    )

    args = argparse.Namespace(
        library="onetbb",
        doc_source="local:docs",
        output_dir=str(tmp_path / "out"),
        model="gpt-6.1-sol",
        provider="openai",
        judge_model="gpt-4o",
        judge_provider="openai",
        registry=None,
        max_tokens=4000,
        concurrency=2,
        questions_from=str(tmp_path / "source"),
    )

    benchmark.cmd_benchmark_preflight(args)

    assert "questions:" in capsys.readouterr().out
