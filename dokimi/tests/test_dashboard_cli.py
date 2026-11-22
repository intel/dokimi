# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`dashboard generate` reports an output location it cannot write as a one-line error."""

from __future__ import annotations

import json

from tests.cli_refusal import refusal

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
        ["dashboard", "generate", "--results-dir", str(results), "--out-dir", str(blocker)])

    with refusal() as out:
        args.func(args)

    assert out.code == 2 and out.err.startswith("Error: "), out.err
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write the dashboard to ")
    assert "Traceback" not in err


def test_generate_writes_a_lone_surrogate_in_a_product_name_as_a_question_mark(tmp_path):
    results = _results(tmp_path)
    eval_file = results / "oneTBB" / "eval_results.json"
    eval_file.write_text(eval_file.read_text(encoding="utf-8").replace("oneTBB", "n\\ud800"), encoding="utf-8")
    out = tmp_path / "out"
    args = build_parser().parse_args(
        ["dashboard", "generate", "--results-dir", str(results), "--out-dir", str(out)])

    args.func(args)

    assert "n?" in (out / "DASHBOARD.md").read_text(encoding="utf-8")


def test_generate_passes_top_n_to_the_dashboard(tmp_path):
    results = _results(tmp_path)
    eval_file = results / "oneTBB" / "eval_results.json"
    doc = json.loads(eval_file.read_text(encoding="utf-8"))
    doc["evaluations"] = [
        {"question_id": f"q{i}", "question": f"Question number {i}?",
         "with_docs": {"aggregate": 10 * i, "correctness": 10 * i},
         "without_docs": {"aggregate": 90, "correctness": 90}, "delta": 10 * i - 90}
        for i in range(1, 5)]
    eval_file.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "out"
    args = build_parser().parse_args(
        ["dashboard", "generate", "--results-dir", str(results), "--out-dir", str(out), "--top-n", "1"])

    args.func(args)

    shown = (out / "DASHBOARD.md").read_text(encoding="utf-8")
    assert shown.count("Question number") == 1


def test_generate_writes_both_files_into_a_fresh_output_dir(tmp_path):
    results = _results(tmp_path)
    out = tmp_path / "out" / "nested"
    args = build_parser().parse_args(
        ["dashboard", "generate", "--results-dir", str(results), "--out-dir", str(out)])

    args.func(args)

    assert "oneTBB" in (out / "DASHBOARD.md").read_text(encoding="utf-8")
    assert json.loads((out / "dashboard.json").read_text(encoding="utf-8"))["products"]
