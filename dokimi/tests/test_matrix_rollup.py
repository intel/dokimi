# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for matrix cell rollup artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.cli import build_parser
from dokimi.commands.arms import _load_questions
from dokimi.eval.matrix_rollup import build_matrix_rollup, strip_internal_payloads
from dokimi.report.matrix_rollup_report import _md_cell, render_matrix_rollup_report


def _arms_artifact(matrix_cell: str, plugin_set: str = "none") -> dict:
    plugin_id = "empty" if plugin_set == "none" else "sha"
    return {
        "schema_version": "arms.v1",
        "library_name": "oneTBB",
        "matrix_cell": matrix_cell,
        "model": "m",
        "provider": "openai",
        "harness": "agent",
        "judge_model": "judge-m",
        "judge_provider": "openai",
        "plugin_set": plugin_set,
        "plugin_set_id": f"sha256:{plugin_id}",
        "plugins": [] if plugin_set == "none" else [{"id": "caveman"}],
        "arms": ["baseline", "skill"],
        "baseline_arm": "baseline",
        "total_questions": 1,
        "answers": [
            {
                "question_id": "q1",
                "arms": {
                    "baseline": {"metrics": {"raw_answer_chars": 100, "final_answer_chars": 100}},
                    "skill": {"metrics": {"raw_answer_chars": 120, "final_answer_chars": 120}},
                },
            }
        ],
        "evaluations": [
            {
                "question_id": "q1",
                "scores": {
                    "baseline": {"aggregate": 70},
                    "skill": {"aggregate": 80 if plugin_set == "none" else 76},
                },
            }
        ],
        "summary": {"per_arm": {"baseline": {"avg_aggregate": 70.0, "n": 1}}},
        "cost_summary": {
            "baseline": {"completion_tokens": 20},
            "skill": {"completion_tokens": 40 if plugin_set == "none" else 30},
        },
    }


def test_build_matrix_rollup_pairs_plugin_cells(tmp_path: Path):
    baseline = _arms_artifact("agent-none")
    plugin = _arms_artifact("agent-caveman", "caveman:full")

    rollup = build_matrix_rollup(
        library_name="oneTBB",
        cell_artifacts=[
            ("agent-none", tmp_path / "agent-none.json", baseline),
            ("agent-caveman", tmp_path / "agent-caveman.json", plugin),
        ],
        out_dir=tmp_path,
    )
    persisted = strip_internal_payloads(rollup)

    validate_artifact("matrix_rollup", persisted)
    assert persisted["total_cells"] == 2
    assert persisted["plugin_deltas"][0]["baseline_cell"] == "agent-none"
    assert persisted["plugin_deltas"][0]["plugin_cell"] == "agent-caveman"
    assert persisted["plugin_deltas"][0]["artifact"].startswith(
        str(tmp_path / "plugin-delta-agent-none-to-agent-caveman-")
    )
    assert "_artifact_payload" not in persisted["plugin_deltas"][0]


def test_matrix_rollup_schema_requires_plugin_delta_artifact():
    rollup = {
        "schema_version": "matrix_rollup.v1",
        "library_name": "oneTBB",
        "cells": [],
        "plugin_deltas": [{"baseline_cell": "a", "plugin_cell": "b"}],
    }

    with pytest.raises(ArtifactValidationError, match="artifact"):
        validate_artifact("matrix_rollup", rollup)


def test_matrix_rollup_report_lists_cells_and_deltas(tmp_path: Path):
    rollup = strip_internal_payloads(build_matrix_rollup(
        library_name="oneTBB",
        cell_artifacts=[
            ("agent-none", tmp_path / "agent-none.json", _arms_artifact("agent-none")),
            (
                "agent-caveman",
                tmp_path / "agent-caveman.json",
                _arms_artifact("agent-caveman", "caveman:full"),
            ),
        ],
        out_dir=tmp_path,
    ))

    md = render_matrix_rollup_report(rollup)

    assert "# Matrix rollup" in md
    assert "agent-none -> agent-caveman" in md


def test_matrix_rollup_report_escapes_pipe_tables():
    assert _md_cell("a | b\nc") == r"a \| b c"
    md = render_matrix_rollup_report({
        "library_name": "oneTBB",
        "cells": [
            {
                "matrix_cell": "a|b",
                "provider": "openai",
                "model": "m\nx",
                "harness": "agent",
                "plugin_set": "none",
                "artifact": "out|path",
            }
        ],
        "plugin_deltas": [
            {
                "baseline_cell": "base|line",
                "plugin_cell": "plug\nin",
                "plugin_set": "cave|man",
                "artifact": "delta|path",
            }
        ],
    })

    assert r"a\|b" in md
    assert r"out\|path" in md
    assert r"base\|line -> plug in" in md


def test_arms_matrix_run_parser_accepts_required_args():
    args = build_parser().parse_args([
        "arms",
        "matrix-run",
        "--product",
        "oneTBB",
        "--questions",
        "questions.json",
        "--arms",
        "baseline,docs",
        "--matrix-cells",
        "matrix.json",
    ])

    assert args.arms_cmd == "matrix-run"
    assert args.matrix_cells == "matrix.json"
    assert args.plugin_deltas is True


def test_load_questions_rejects_non_object_items(tmp_path: Path):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(["bad"]), encoding="utf-8")

    with pytest.raises(ValueError, match="question at index 0"):
        _load_questions(str(questions))


def test_arms_matrix_run_rejects_sanitized_cell_name_collisions(tmp_path: Path):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {"id": "openai/gpt-4o", "model": "m", "provider": "openai", "harness": "arms-runner"},
                    {"id": "openai:gpt-4o", "model": "m", "provider": "openai", "harness": "arms-runner"},
                ]
            }
        }),
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "arms",
        "matrix-run",
        "--product",
        "oneTBB",
        "--questions",
        str(questions),
        "--arms",
        "baseline",
        "--matrix-cells",
        str(matrix),
        "--out-dir",
        str(tmp_path / "out"),
    ])

    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1


def test_arms_matrix_run_rejects_unwired_harnesses(tmp_path: Path):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {
                        "id": "terminal",
                        "model": "m",
                        "provider": "openai",
                        "harness": "agent",
                    }
                ]
            }
        }),
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "arms",
        "matrix-run",
        "--product",
        "oneTBB",
        "--questions",
        str(questions),
        "--arms",
        "baseline",
        "--matrix-cells",
        str(matrix),
    ])

    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1


def test_arms_run_selected_matrix_cell_rejects_unwired_harness(tmp_path: Path):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {
                        "id": "agent-cell",
                        "model": "m",
                        "provider": "openai",
                        "harness": "agent",
                    }
                ]
            }
        }),
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "arms",
        "run",
        "--product",
        "oneTBB",
        "--questions",
        str(questions),
        "--arms",
        "baseline",
        "--matrix-cells",
        str(matrix),
        "--matrix-cell",
        "agent-cell",
    ])

    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1


def test_arms_matrix_run_keeps_cell_artifacts_away_from_generated_names(tmp_path: Path, monkeypatch):
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        return "answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {"id": "matrix-rollup", "model": "m", "provider": "openai", "harness": "arms-runner"}
                ]
            }
        }),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    args = build_parser().parse_args([
        "arms",
        "matrix-run",
        "--product",
        "oneTBB",
        "--questions",
        str(questions),
        "--arms",
        "baseline",
        "--matrix-cells",
        str(matrix),
        "--out-dir",
        str(out_dir),
    ])

    args.func(args)

    assert json.loads((out_dir / "matrix-rollup.json").read_text(encoding="utf-8"))["schema_version"] == "matrix_rollup.v1"
    assert json.loads((out_dir / "cells" / "matrix-rollup.json").read_text(encoding="utf-8"))["schema_version"] == "arms.v1"


def test_arms_matrix_run_writes_cells_rollup_and_plugin_delta(tmp_path: Path, monkeypatch):
    def fake_call(prompt, model, provider="openai", api_key=None, system=None, **kw):
        return "short answer" if system else "longer baseline answer", {
            "prompt_tokens": 10,
            "completion_tokens": 5 if system else 8,
            "total_tokens": 15 if system else 18,
        }

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake_call)
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "How?"}]), encoding="utf-8")
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps({
            "matrix": {
                "cells": [
                    {
                        "name": "agent-none",
                        "model": "m",
                        "provider": "openai",
                        "harness": "arms-runner",
                        "plugins": [],
                    },
                    {
                        "name": "agent-caveman",
                        "model": "m",
                        "provider": "openai",
                        "harness": "arms-runner",
                        "plugins": ["plugin:caveman"],
                    },
                ]
            }
        }),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    args = build_parser().parse_args([
        "arms",
        "matrix-run",
        "--product",
        "oneTBB",
        "--questions",
        str(questions),
        "--arms",
        "baseline",
        "--matrix-cells",
        str(matrix),
        "--out-dir",
        str(out_dir),
    ])

    args.func(args)

    assert (out_dir / "cells" / "agent-none.json").is_file()
    assert (out_dir / "cells" / "agent-caveman.json").is_file()
    assert (out_dir / "matrix-rollup.json").is_file()
    assert list(out_dir.glob("plugin-delta-agent-none-to-agent-caveman-*.json"))


def test_load_questions_refuses_duplicate_ids(tmp_path: Path):
    """A shared id makes the judge's context ambiguous for every answer under it."""
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]))
    with pytest.raises(ValueError, match="duplicate question ids"):
        _load_questions(str(questions))


def test_load_questions_assigns_unique_ids_where_missing(tmp_path: Path, capsys):
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"text": "x"}, {"id": "q0001", "text": "y"}, {"text": "z"}]))
    loaded = _load_questions(str(questions))
    ids = [q["id"] for q in loaded]
    assert ids[1] == "q0001" and len(set(ids)) == 3
    assert "had no id" in capsys.readouterr().err


def test_load_questions_keeps_integer_id_zero(tmp_path: Path):
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": 0, "text": "x"}, {"id": 1, "text": "y"}]))
    assert [q["id"] for q in _load_questions(str(questions))] == [0, 1]


def test_question_id_keeps_integer_id_zero():
    assert arm_runner_mod.question_id({"id": 0}) == 0
    assert arm_runner_mod.question_id({"id": "", "question_id": "b"}) == "b"
    assert arm_runner_mod.question_id({}) is None
