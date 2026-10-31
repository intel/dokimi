# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`dashboard generate` reports an output location it cannot write as a one-line error."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser


def _results(tmp_path):
    eval_file = tmp_path / "results" / "oneTBB" / "eval_results.json"
    eval_file.parent.mkdir(parents=True)
    eval_file.write_text(json.dumps({
        "library_name": "oneTBB",
        "evaluated_at": "2026-02-27T00:00:00Z",
        "judge_model": "judge",
        "total_evaluations": 1,
        "evaluations": [{
            "question_id": "q1",
            "question": "How to use parallel_for?",
            "with_docs": {"aggregate": 80, "correctness": 80},
            "without_docs": {"aggregate": 60, "correctness": 60},
            "delta": 20,
        }],
    }), encoding="utf-8")
    return tmp_path / "results"


def test_generate_exits_2_with_a_message_when_the_output_dir_is_a_file(tmp_path, capsys):
    results = _results(tmp_path)
    blocker = tmp_path / "out"
    blocker.write_text("not a directory", encoding="utf-8")
    args = build_parser().parse_args(
        ["dashboard", "generate", "--results-dir", str(results), "--output-dir", str(blocker)])

    with pytest.raises(SystemExit) as exc:
        args.func(args)

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write the dashboard to ")
    assert "Traceback" not in err


def test_generate_writes_both_files_into_a_fresh_output_dir(tmp_path):
    results = _results(tmp_path)
    out = tmp_path / "out" / "nested"
    args = build_parser().parse_args(
        ["dashboard", "generate", "--results-dir", str(results), "--output-dir", str(out)])

    args.func(args)

    assert "oneTBB" in (out / "DASHBOARD.md").read_text(encoding="utf-8")
    assert json.loads((out / "dashboard.json").read_text(encoding="utf-8"))["products"]
