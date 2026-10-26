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
    assert f"Error: cannot write {target}: " in capsys.readouterr().err


class _Report:
    verdicts = [object()]
    errors: list = []
    measures = [object()]

    def __init__(self, *a, **k):
        pass

    def to_dict(self):
        return {}

    def format_markdown(self):
        return "md"

    def apply_validity(self, validity):
        return []

    def summary(self):
        return {"arms": {}, "measured": True, "fragile_variants": []}

    def format_summary(self):
        return ""


def _stub_judge_commands(monkeypatch):
    """Replace every judge-facing dependency of the four judge_reliability writers with a stub."""
    import types

    import dokimi.eval.checklist as checklist
    import dokimi.eval.cross_judge as cross_judge
    import dokimi.eval.judge as judge
    import dokimi.eval.judge_validity as judge_validity
    import dokimi.eval.judge_wording as judge_wording
    import dokimi.eval.pairwise as pairwise

    monkeypatch.setattr(judge_reliability, "_load_answers", lambda path: (None, []))
    monkeypatch.setattr(judge_reliability, "_load_points", lambda path: {})
    monkeypatch.setattr(judge_reliability, "_require_arms", lambda *a, **k: None)
    monkeypatch.setattr(judge_reliability, "_check_judge_temperature", lambda *a, **k: None)
    monkeypatch.setattr(judge_reliability, "pairwise_judge", lambda args: (None, "label"))
    monkeypatch.setattr(pairwise, "pairwise_jobs", lambda *a, **k: None)
    monkeypatch.setattr(pairwise, "run_arm_pairwise", lambda *a, **k: _Report())
    monkeypatch.setattr(checklist, "checklist_jobs", lambda *a, **k: None)
    monkeypatch.setattr(checklist, "run_checklist", lambda *a, **k: _Report())
    monkeypatch.setattr(judge, "Judge", lambda **kw: types.SimpleNamespace(llm=None))
    monkeypatch.setattr(judge_validity, "_build", lambda *a, **k: types.SimpleNamespace(pairs=[]))
    monkeypatch.setattr(judge_wording, "run_wording", lambda *a, **k: _Report())
    monkeypatch.setattr(cross_judge, "CrossJudgeReport", _Report)
    monkeypatch.setattr(cross_judge, "scored_answers", lambda *a, **k: None)
    monkeypatch.setattr(cross_judge, "score_measure", lambda *a, **k: object())


_JUDGE_COMMANDS = {
    "pairwise": ["eval", "pairwise"],
    "checklist": ["eval", "checklist", "--questions", "q.json"],
    "judge-wording": ["eval", "judge-wording"],
    "cross-judge": ["eval", "cross-judge", "--judges", "openai/m", "--no-stored"],
}
_WRITES_MD = ["pairwise", "checklist", "cross-judge"]


def _run(command, out):
    args = build_parser().parse_args(
        [*_JUDGE_COMMANDS[command], "--answers", "a.json", "--output", str(out)]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    return exc.value.code


@pytest.mark.parametrize("command", list(_JUDGE_COMMANDS))
def test_judge_reliability_json_write_failure_exits_2_naming_the_path(
    command, tmp_path, monkeypatch, capsys
):
    _stub_judge_commands(monkeypatch)
    out = _blocked(tmp_path)
    assert _run(command, out) == 2
    assert f"Error: cannot write {out}" in capsys.readouterr().err


@pytest.mark.parametrize("command", _WRITES_MD)
def test_judge_reliability_md_sidecar_write_failure_exits_2_naming_the_path(
    command, tmp_path, monkeypatch, capsys
):
    _stub_judge_commands(monkeypatch)
    out = tmp_path / "out.json"
    sidecar = out.with_suffix(".md")
    sidecar.mkdir()
    assert _run(command, out) == 2
    assert f"Error: cannot write {sidecar}" in capsys.readouterr().err


def test_ragas_results_that_cannot_be_written_exit_2_naming_the_path(tmp_path, monkeypatch, capsys):
    import types

    import dokimi.eval.ragas_eval as ragas_eval
    from dokimi.commands.evaluate import _run_ragas_eval

    class Evaluator:
        def __init__(self, **kw):
            pass

        def evaluate(self, answers, include_without_docs=True):
            return types.SimpleNamespace(to_dict=dict, format_summary=lambda: "")

    monkeypatch.setattr(ragas_eval, "RagasEvaluator", Evaluator)
    blocked = _blocked(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_ragas_eval([], blocked, types.SimpleNamespace())
    assert exc.value.code == 2
    assert f"Error: cannot write {blocked.with_name('out_ragas.json')}" in capsys.readouterr().err


def test_panel_score_output_that_cannot_be_written_exits_2_naming_the_path(
    tmp_path, monkeypatch, capsys
):
    import types

    import dokimi.eval.panel as panel_mod
    from dokimi.commands import evaluate

    target = tmp_path / "p.json"

    class Panel:
        def __init__(self, **kw):
            pass

        def evaluate_answers(self, *a, **k):
            raise PermissionError(13, "denied", f"{target}.123.0.tmp")

    monkeypatch.setattr(panel_mod, "JudgePanel", Panel)
    monkeypatch.setattr(
        evaluate, "_load_answers", lambda path: types.SimpleNamespace(records=[], meta={})
    )
    args = build_parser().parse_args(
        ["eval", "panel-score", "--product", "P", "--answers", "a.json", "--output", str(target)]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert f"Error: cannot write {target}: " in capsys.readouterr().err


def test_score_output_that_cannot_be_written_exits_2_naming_the_path(tmp_path, monkeypatch, capsys):
    class Judge:
        def evaluate_answers(self, *a, **k):
            return []

        def save_evaluations(self, evals, path):
            raise PermissionError(13, "Permission denied", f"{path}.123.0.tmp")

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": []}), encoding="utf-8")
    import dokimi.eval as eval_mod

    monkeypatch.setattr(eval_mod, "Judge", lambda **kw: Judge())
    target = tmp_path / "e.json"
    args = build_parser().parse_args(
        ["eval", "score", "--product", "P", "--answers", str(answers), "--output", str(target)]
    )
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert f"Error: cannot write {target}: " in capsys.readouterr().err


def test_report_eval_names_the_sidecar_when_it_cannot_be_written(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    eval_file, out = tmp_path / "eval.json", tmp_path / "r.md"
    eval_file.write_text(
        json.dumps(
            {
                "evaluations": [
                    {
                        "question_id": "q1",
                        "question_text": "Q?",
                        "with_docs": {"aggregate": 80},
                        "without_docs": {"aggregate": 60},
                        "delta": 20,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "r.qa.json").mkdir()
    monkeypatch.setattr(
        "sys.argv",
        ["dokimi", "report", "eval", "--product", "P", "--eval", str(eval_file), "--out", str(out)],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(tmp_path / "r.qa.json") in err and f"cannot write {out}" not in err


def test_report_eval_sidecar_does_not_overwrite_an_out_without_md(tmp_path, monkeypatch):
    from dokimi import cli

    eval_file, out = tmp_path / "eval.json", tmp_path / "report"
    eval_file.write_text(
        json.dumps(
            {
                "evaluations": [
                    {
                        "question_id": "q1",
                        "question_text": "Q?",
                        "with_docs": {"aggregate": 80},
                        "without_docs": {"aggregate": 60},
                        "delta": 20,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["dokimi", "report", "eval", "--product", "P", "--eval", str(eval_file), "--out", str(out)],
    )
    cli.main()
    assert not out.read_text(encoding="utf-8").lstrip().startswith("{")
    assert (tmp_path / "report.qa.json").exists()
