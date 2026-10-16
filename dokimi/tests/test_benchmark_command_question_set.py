# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Command-level tests for fixed question-set benchmark runs."""

from __future__ import annotations

import argparse
import json

import pytest

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


@pytest.mark.outcome("pinned-questions-reuse")
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
    (questions_root / "onetbb_golden.json").write_text(
        json.dumps({"questions": [{"id": "g1", "question": "How?"}]}), encoding="utf-8"
    )

    args = build_parser().parse_args([
        "benchmark", "preflight", "--library", "onetbb",
        "--doc-source", "local:docs",
        "--output-dir", str(tmp_path / "out"),
        "--questions-root", str(questions_root),
        "--provider", "openai", "--judge-provider", "openai",
    ])
    args.func(args)

    out = capsys.readouterr().out
    assert f"questions_root: {questions_root}" in out
    assert "golden questions:" in out and "(1 items)" in out


@pytest.mark.outcome("pinned-questions-preflight")
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


def _preflight(monkeypatch, tmp_path, *extra, env=("OPENAI_API_KEY",)):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key in env:
        monkeypatch.setenv(key, "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(
        "dokimi.mcp.factory.create_doc_source_client",
        lambda doc_source: object(),
    )
    args = build_parser().parse_args([
        "benchmark", "preflight", "--library", "onetbb",
        "--doc-source", "local:docs",
        "--provider", "openai", "--judge-provider", "openai",
        *extra,
    ])
    if "--output-dir" not in extra:
        args.output_dir = str(tmp_path / "out")
    args.func(args)


def _bad_questions_from(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "onetbb.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    return ["--questions-from", str(tmp_path / "bad" / "onetbb.json")]


def _empty_root(tmp_path):
    (tmp_path / "questions").mkdir()
    return ["--questions-root", str(tmp_path / "questions")]


def _root_with_invalid_golden(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "onetbb_golden.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    return ["--questions-root", str(tmp_path / "questions")]


def _root_with_empty_golden(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "onetbb_golden.json").write_text(json.dumps({"questions": []}), encoding="utf-8")
    return ["--questions-root", str(tmp_path / "questions")]


def _root_with_bare_strings_golden(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "onetbb_golden.json").write_text(json.dumps(["a", "b"]), encoding="utf-8")
    return ["--questions-root", str(tmp_path / "questions")]


def _root_with_null_items_golden(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "onetbb_golden.json").write_text(json.dumps([None, 3]), encoding="utf-8")
    return ["--questions-root", str(tmp_path / "questions")]


def _root_with_mixed_dict_string_golden(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "onetbb_golden.json").write_text(json.dumps([{"id": "a"}, "b"]), encoding="utf-8")
    return ["--questions-root", str(tmp_path / "questions")]


@pytest.mark.outcome("pinned-questions-preflight")
@pytest.mark.parametrize(
    "preflight_args, env, message",
    [
        (lambda p: [], (), "missing OPENAI_API_KEY"),
        (lambda p: ["--questions-from", str(p / "nowhere")], ("OPENAI_API_KEY",), "questions source not found"),
        (_bad_questions_from, ("OPENAI_API_KEY",), "questions source invalid"),
        (lambda p: ["--questions-root", str(p / "nowhere")], ("OPENAI_API_KEY",), "questions root not found"),
        (_empty_root, ("OPENAI_API_KEY",), "questions root has no golden set"),
        (_root_with_invalid_golden, ("OPENAI_API_KEY",), "golden set invalid"),
        (_root_with_empty_golden, ("OPENAI_API_KEY",), "golden set is empty"),
        (_root_with_bare_strings_golden, ("OPENAI_API_KEY",), "is not a dict"),
        (_root_with_null_items_golden, ("OPENAI_API_KEY",), "is not a dict"),
        (_root_with_mixed_dict_string_golden, ("OPENAI_API_KEY",), "is not a dict"),
    ],
    ids=[
        "no-key", "questions-from-missing", "questions-from-invalid",
        "root-missing", "root-without-golden", "root-golden-invalid", "root-golden-empty",
        "root-golden-strings", "root-golden-nulls", "root-golden-mixed",
    ],
)
def test_preflight_fails_before_any_call(monkeypatch, tmp_path, capsys, preflight_args, env, message):
    with pytest.raises(SystemExit) as exc:
        _preflight(monkeypatch, tmp_path, *preflight_args(tmp_path), env=env)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert message in captured.err
    assert "Preflight OK" not in captured.out


@pytest.mark.outcome("pinned-questions-preflight")
def test_preflight_questions_from_skips_root_check(monkeypatch, tmp_path, capsys):
    """When --questions-from is set, preflight does not require a golden set in the root."""
    questions_dir = tmp_path / "source" / "questions"
    questions_dir.mkdir(parents=True)
    (questions_dir / "onetbb.json").write_text(
        json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8"
    )

    empty_root = tmp_path / "empty_questions"
    empty_root.mkdir()

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(
        "dokimi.mcp.factory.create_doc_source_client",
        lambda doc_source: object(),
    )

    args = build_parser().parse_args([
        "benchmark", "preflight", "--library", "onetbb",
        "--doc-source", "local:docs",
        "--output-dir", str(tmp_path / "out"),
        "--questions-from", str(questions_dir / "onetbb.json"),
        "--questions-root", str(empty_root),
        "--provider", "openai", "--judge-provider", "openai",
    ])
    args.func(args)

    assert "Preflight OK" in capsys.readouterr().out


def test_preflight_rejects_an_unwritable_output_dir(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        _preflight(monkeypatch, tmp_path, "--output-dir", str(blocker / "out"))

    assert exc.value.code == 1
    assert "output dir not writable" in capsys.readouterr().err


@pytest.mark.outcome("pinned-questions-reuse")
def test_multi_run_forwards_questions_root_to_every_run(monkeypatch, tmp_path):
    """Every run in a multi-run forwards the same --questions-root."""
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

    questions_root = tmp_path / "content-repo" / "questions"
    questions_root.mkdir(parents=True)
    (questions_root / "onetbb_golden.json").write_text(
        json.dumps({"questions": [{"id": "g1", "question": "How?"}]}), encoding="utf-8"
    )

    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _Registry())
    monkeypatch.setattr(benchmark, "_run_single_library", fake_run_single_library)

    output_dir = tmp_path / "onetbb_eval"
    args = argparse.Namespace(
        library="onetbb",
        output_dir=str(output_dir),
        model="gpt-4o-mini",
        provider="openai",
        judge_model="gpt-4o",
        judge_provider="openai",
        doc_source=None,
        max_tokens=4000,
        force_regen=False,
        concurrency=2,
        questions_from=None,
        questions_root=str(questions_root),
        multi_run=3,
    )

    benchmark.cmd_benchmark_run(args)

    assert [call["questions_root"] for call in calls] == [
        str(questions_root),
        str(questions_root),
        str(questions_root),
    ]
