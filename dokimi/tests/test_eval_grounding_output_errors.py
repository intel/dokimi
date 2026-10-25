# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval grounding` and `eval judge-validity`: an unwritable --output exits 1 with a message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser


def _grounding(tmp_path: Path, output: Path) -> None:
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": [
        {"question_id": "q1", "question_text": "How do I loop?",
         "with_docs": {"answer": "use parallel_for",
                       "retrieved_docs": [{"snippet": "parallel_for runs tasks"}]}},
    ]}), encoding="utf-8")
    args = build_parser().parse_args(["eval", "grounding", "--answers", str(answers), "--output", str(output)])
    args.func(args)


def test_grounding_report_is_written(tmp_path):
    out = tmp_path / "nested" / "grounding.json"
    _grounding(tmp_path, out)
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == "grounding.v1"


def test_output_below_a_file_exits_1_naming_the_path(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    target = blocker / "grounding.json"
    with pytest.raises(SystemExit) as exc:
        _grounding(tmp_path, target)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"Error: cannot write {target}" in err
    assert "Traceback" not in err


def test_judge_validity_output_below_a_file_exits_1_naming_the_path(tmp_path, monkeypatch, capsys):
    import dokimi.eval.judge as judge_mod
    import dokimi.eval.judge_validity as jv

    answer = ("Use perf stat to collect IPC, then narrow the hotspot with VTune. "
              "Enable AVX-512 in the build and expect roughly 1.5x on 8 threads. ") * 12
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([
        {"question_id": f"Q{i}", "question_text": "q", "arms": {"a": {"answer": f"{i}. " + answer}}}
        for i in range(20)
    ]), encoding="utf-8")
    monkeypatch.setattr(judge_mod, "Judge", lambda **kw: None)
    monkeypatch.setattr(jv, "judge_scorer", lambda judge: lambda lib, q, a: float(len(a)))
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "validity.json"
    args = build_parser().parse_args(["eval", "judge-validity", "--answers", str(answers), "--bases", "20",
                                      "--variants", "truncate_20", "--output", str(target)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_output_that_is_a_directory_exits_1_naming_the_path(tmp_path, capsys):
    target = tmp_path / "is-a-dir"
    target.mkdir()
    with pytest.raises(SystemExit) as exc:
        _grounding(tmp_path, target)
    assert exc.value.code == 1
    assert f"Error: cannot write {target}" in capsys.readouterr().err
