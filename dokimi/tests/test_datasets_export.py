# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for artifact -> dataset export."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dokimi.datasets_export import dataset_card, export_artifact, flatten

HAS_DATASETS = importlib.util.find_spec("datasets") is not None


def test_flatten_attaches_metadata_and_jsonifies():
    data = {
        "schema_version": "answers.v1",
        "model": "gpt-4o",
        "answers": [
            {"question_id": "q1", "with_docs": {"answer": "a", "model": "gpt-4o"}},
        ],
    }
    rows = flatten("answers", data)
    assert rows[0]["model"] == "gpt-4o"
    assert rows[0]["question_id"] == "q1"
    # nested dict is JSON-encoded
    assert json.loads(rows[0]["with_docs"])["answer"] == "a"


def test_export_jsonl(tmp_path):
    src = tmp_path / "q.json"
    src.write_text(json.dumps({
        "schema_version": "questions.v1",
        "library": "oneTBB",
        "questions": [{"id": "q1", "text": "What is X?"}, {"id": "q2", "text": "How Y?"}],
    }))
    out = tmp_path / "out"
    primary = export_artifact("questions", src, out, fmt="jsonl")
    assert primary.exists()
    lines = primary.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["library"] == "oneTBB"
    assert (out / "README.md").exists()


def test_export_answers_artifact_jsonl(tmp_path):
    """The answers kind flattens a nested per-arm record, which the questions kind does not
    exercise. This used to point at a captured product run under a path that never existed,
    so it skipped on every run; the payload is written here instead.
    """
    src = tmp_path / "a.json"
    src.write_text(json.dumps({
        "schema_version": "answers.v1",
        "model": "gpt-6.1-sol",
        "provider": "openai",
        "total_questions": 1,
        "answers": [
            {
                "question_id": "q1",
                "question_text": "What is X?",
                "library_name": "demo",
                "with_docs": {"answer": "a", "tokens": 3},
                "without_docs": {"answer": "b", "tokens": 2},
            }
        ],
    }))
    primary = export_artifact("answers", src, tmp_path / "out", fmt="jsonl")
    assert primary.exists()
    rows = [json.loads(line) for line in primary.read_text().strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["question_id"] == "q1"
    assert rows[0]["model"] == "gpt-6.1-sol"
    assert json.loads(rows[0]["with_docs"])["answer"] == "a"


def test_dataset_card_contents():
    card = dataset_card("questions", Path("my_questions.json"), 5, ["id", "question"])
    assert "Schema version:** questions.v2" in card
    assert "Source schema version" not in card
    assert "Rows:** 5" in card


@pytest.mark.skipif(not HAS_DATASETS, reason="datasets not installed")
def test_export_parquet(tmp_path):
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"schema_version": "questions.v1",
                               "questions": [{"id": "q1", "text": "x"}]}))
    primary = export_artifact("questions", src, tmp_path / "out", fmt="parquet")
    assert primary.exists()


def test_flatten_task_runs_lifts_metrics_to_columns():
    data = {
        "schema_version": "task_runs.v1",
        "model": "haiku",
        "results": [
            {"task": "t1", "harness": "docker-claude", "passed": True, "reward": 1.0,
             "metrics": {"operation_count": 1, "operations_by_type": {"harness": 1},
                         "cost_usd": 0.05, "n_calls": 7, "total_tokens": 600, "mode": "claude"}},
        ],
    }
    rows = flatten("task_runs", data)
    assert rows[0]["task"] == "t1"
    assert rows[0]["model"] == "haiku"
    # telemetry lifted to flat, typed columns
    assert rows[0]["metric_cost_usd"] == 0.05
    assert rows[0]["metric_n_calls"] == 7
    assert rows[0]["metric_mode"] == "claude"
    # full metrics block still present as a JSON-encoded column
    assert json.loads(rows[0]["metrics"])["operation_count"] == 1


def test_export_task_runs_jsonl(tmp_path):
    src = tmp_path / "tr.json"
    src.write_text(json.dumps({
        "schema_version": "task_runs.v1",
        "harnesses": ["docker-claude"],
        "tasks": [{"name": "t1", "path": "p", "metadata": {}, "verifier": {},
                   "agent": {}, "environment": {}}],
        "results": [{"task": "t1", "harness": "docker-claude", "passed": False,
                     "metrics": {"operation_count": 0, "operations_by_type": {}},
                     "operations": []}],
        "summary": {"per_harness": {}, "comparisons": {}},
    }))
    primary = export_artifact("task_runs", src, tmp_path / "out", fmt="jsonl")
    assert primary.exists()
    assert json.loads(primary.read_text().strip())["task"] == "t1"


def _run_export(src: Path, out: Path, kind: str = "answers", *extra: str) -> None:
    from dokimi.cli import build_parser

    args = build_parser().parse_args(["dataset", "export", "--kind", kind, "--input", str(src),
                                      "--out-dir", str(out), *extra])
    args.func(args)


def _export_error(src: Path, out: Path, *extra: str, capsys) -> str:
    with pytest.raises(SystemExit) as exc:
        _run_export(src, out, "answers", *extra)
    assert exc.value.code == 2
    assert exc.value.__suppress_context__
    assert not (out / "README.md").exists()
    err = capsys.readouterr().err
    assert err.startswith(f"Error: cannot export {src} to {out}: ")
    return err


def test_cli_export_of_a_missing_input_exits_cleanly(tmp_path, capsys):
    err = _export_error(tmp_path / "nope.json", tmp_path / "out", capsys=capsys)
    assert "nope.json" in err and "Traceback" not in err


def test_cli_export_to_an_unwritable_out_dir_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"schema_version": "answers.v1", "answers": []}), encoding="utf-8")
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    err = _export_error(src, blocker / "out", capsys=capsys)
    assert "cannot export" in err and "a.json" in err


def test_cli_export_of_invalid_json_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text("{not json", encoding="utf-8")
    assert "a.json" in _export_error(src, tmp_path / "out", capsys=capsys)


def test_cli_export_of_a_schema_invalid_artifact_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"schema_version": "answers.v1", "answers": "x"}), encoding="utf-8")
    assert "validation failed" in _export_error(src, tmp_path / "out", capsys=capsys)


def test_cli_export_without_validation_of_a_wrong_shape_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"not_answers": []}), encoding="utf-8")
    err = _export_error(src, tmp_path / "out", "--no-validate", capsys=capsys)
    assert err.startswith(f"Error: cannot export {src} to {tmp_path / 'out'}: Artifact")


def test_cli_export_without_validation_of_a_non_list_record_array_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"answers": {}}), encoding="utf-8")
    err = _export_error(src, tmp_path / "out", "--no-validate", capsys=capsys)
    assert "must be a list" in err and "Traceback" not in err


def test_flatten_of_an_unknown_kind_raises():
    with pytest.raises(ValueError, match="Unknown artifact kind"):
        flatten("bogus", {})


@pytest.mark.parametrize("raw", ["[]", '"s"', "null", "3"])
@pytest.mark.parametrize("extra", [(), ("--no-validate",)])
@pytest.mark.parametrize("kind", ["answers", "eval", "arms", "task_runs"])
def test_cli_export_of_a_non_object_top_level_exits_cleanly(tmp_path, capsys, raw, extra, kind):
    src = tmp_path / "a.json"
    src.write_text(raw, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run_export(src, tmp_path / "out", kind, *extra)
    assert exc.value.code == 2
    assert not (tmp_path / "out" / "README.md").exists()
    assert capsys.readouterr().err.startswith(f"Error: cannot export {src} to {tmp_path / 'out'}: ")


def test_flatten_missing_record_key_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("answers", {"not_answers": []})


def test_flatten_non_list_records_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("answers", {"answers": {"q": 1}})
