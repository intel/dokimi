# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval writers and report eval: an unwritable output exits with a message naming the failing path."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands import judge_reliability


def _blocked(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    return blocker / "out.json"



def test_write_text_or_exit_names_the_path_and_exits_with_the_code(tmp_path, capsys):
    from dokimi.utils import write_text_or_exit

    target = _blocked(tmp_path)
    with pytest.raises(SystemExit) as exc:
        write_text_or_exit(target, "{}", code=2)
    assert exc.value.code == 2
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_judge_reliability_writers_use_the_guarded_write():
    import inspect

    src = inspect.getsource(judge_reliability)
    assert ".write_text(" not in src and "mkdir(" not in src


def test_score_output_that_cannot_be_written_exits_2_naming_the_path(tmp_path, monkeypatch, capsys):
    class Judge:
        def evaluate_answers(self, *a, **k):
            return []

        def save_evaluations(self, evals, path):
            raise PermissionError(13, "Permission denied", str(path))

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    import dokimi.eval as eval_mod

    monkeypatch.setattr(eval_mod, "Judge", lambda **kw: Judge())
    target = tmp_path / "e.json"
    args = build_parser().parse_args(["eval", "score", "--product", "P", "--answers", str(answers),
                                      "--output", str(target)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_report_eval_names_the_sidecar_when_it_cannot_be_written(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    eval_file, out = tmp_path / "eval.json", tmp_path / "r.md"
    eval_file.write_text(json.dumps({"evaluations": [{
        "question_id": "q1", "question_text": "Q?", "with_docs": {"aggregate": 80},
        "without_docs": {"aggregate": 60}, "delta": 20}]}), encoding="utf-8")
    (tmp_path / "r.qa.json").mkdir()
    monkeypatch.setattr("sys.argv", ["dokimi", "report", "eval", "--product", "P", "--eval", str(eval_file),
                                     "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(tmp_path / "r.qa.json") in err and f"cannot write {out}" not in err


def test_report_eval_sidecar_does_not_overwrite_an_out_without_md(tmp_path, monkeypatch):
    from dokimi import cli

    eval_file, out = tmp_path / "eval.json", tmp_path / "report"
    eval_file.write_text(json.dumps({"evaluations": [{
        "question_id": "q1", "question_text": "Q?", "with_docs": {"aggregate": 80},
        "without_docs": {"aggregate": 60}, "delta": 20}]}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "report", "eval", "--product", "P", "--eval", str(eval_file),
                                     "--out", str(out)])
    cli.main()
    assert not out.read_text(encoding="utf-8").lstrip().startswith("{")
    assert (tmp_path / "report.qa.json").exists()
