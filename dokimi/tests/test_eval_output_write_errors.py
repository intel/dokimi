# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval writers and report eval: an unwritable output exits with a message naming the failing path."""

from __future__ import annotations

import json
import os

import pytest

from dokimi.cli import build_parser
from dokimi.commands import judge_reliability


from tests.cli_refusal import refusal

def _blocked(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    return blocker / "out.json"


def test_write_text_or_exit_names_the_path_and_exits_with_the_code(tmp_path, capsys):
    from dokimi.commands._output import write_text_or_exit

    target = _blocked(tmp_path)
    with refusal() as exc:
        write_text_or_exit(target, "{}", code=2)
    assert exc.code == 2
    assert f"Error: cannot write {target}: " in exc.err


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
    "pairwise": ["eval", "score", "pairwise"],
    "checklist": ["eval", "score", "checklist", "--questions", "q.json"],
    "judge-wording": ["eval", "judge", "wording"],
    "cross-judge": ["eval", "judge", "agreement", "--judges", "openai/m", "--no-stored"],
}
_WRITES_MD = ["pairwise", "checklist", "cross-judge"]


def _run(command, out):
    args = build_parser().parse_args(
        [*_JUDGE_COMMANDS[command], "--answers", "a.json", "--output", str(out)]
    )
    with refusal() as exc:
        args.func(args)
    return exc.code


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
    with refusal() as exc:
        _run_ragas_eval([], blocked, types.SimpleNamespace())
    assert exc.code == 2
    assert f"Error: cannot write {blocked.with_name('out_ragas.json')}" in exc.err


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
        ["eval", "score", "panel", "--product", "P", "--answers", "a.json", "--output", str(target)]
    )
    with refusal() as exc:
        args.func(args)
    assert exc.code == 2
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
        ["eval", "score", "pointwise", "--product", "P", "--answers", str(answers), "--output", str(target)]
    )
    with refusal() as exc:
        args.func(args)
    assert exc.code == 2
    assert f"Error: cannot write {target}: " in exc.err


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
    with refusal() as exc:
        cli.main()
    assert exc.code == 2
    err = exc.err
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


@pytest.mark.parametrize(("text", "expected"), [
    ("# n\ud800\n", b"# n?\n"),
    ("a\ud83d\ude00b\n", "a\U0001F600b\n".encode("utf-8")),
    ("café\n", "café\n".encode("utf-8")),
], ids=["lone-surrogate", "surrogate-pair", "non-ascii"])
def test_write_report_text_writes_a_lone_surrogate_as_a_question_mark_and_joins_a_pair(tmp_path, text, expected):
    import os

    from dokimi.commands._output import write_report_text

    target = tmp_path / "new" / "dir" / "r.md"
    write_report_text(target, text)
    assert target.read_bytes() == expected.replace(b"\n", os.linesep.encode())


def test_write_report_text_writes_the_bytes_write_text_writes_for_text_that_encodes(tmp_path):
    from dokimi.commands._output import write_report_text

    text = "# tést\r\nline\n\n| a | ✓ |\n"
    ours, theirs = tmp_path / "ours.md", tmp_path / "theirs.md"
    write_report_text(ours, text)
    theirs.write_text(text, encoding="utf-8")
    assert ours.read_bytes() == theirs.read_bytes()


def test_write_report_text_writes_each_newline_as_the_platform_linesep(tmp_path, monkeypatch):
    from dokimi.commands._output import write_report_text

    monkeypatch.setattr("os.linesep", "\r\n")
    write_report_text(tmp_path / "r.md", "a\nb\r\n")
    assert (tmp_path / "r.md").read_bytes() == b"a\r\nb\r\r\n"


def test_write_report_text_refuses_a_read_only_report_and_keeps_its_text(tmp_path):
    import os

    from dokimi.commands._output import write_report_text

    target = tmp_path / "r.md"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o444)
    if os.access(target, os.W_OK):
        pytest.skip("the user can write a read-only file")
    with pytest.raises(PermissionError):
        write_report_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "old"


def test_write_report_text_writes_through_a_symlink_and_keeps_the_link(tmp_path):
    from dokimi.commands._output import write_report_text

    real, link = tmp_path / "real.md", tmp_path / "link.md"
    real.write_text("old", encoding="utf-8")
    link.symlink_to(real)
    write_report_text(link, "new\n")
    assert link.is_symlink() and real.read_text(encoding="utf-8") == "new\n"


def test_write_report_text_keeps_a_hard_link_shared(tmp_path):
    import os

    from dokimi.commands._output import write_report_text

    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("old", encoding="utf-8")
    os.link(first, second)
    write_report_text(first, "new\n")
    assert second.read_text(encoding="utf-8") == "new\n"


def test_write_report_text_rewrites_a_writable_file_in_a_read_only_directory(tmp_path):
    import os

    from dokimi.commands._output import write_report_text

    folder = tmp_path / "ro"
    folder.mkdir()
    target = folder / "r.md"
    target.write_text("old", encoding="utf-8")
    folder.chmod(0o555)
    try:
        if os.access(folder, os.W_OK):
            pytest.skip("the user can write a read-only directory")
        write_report_text(target, "new\n")
        assert target.read_text(encoding="utf-8") == "new\n"
        assert [p.name for p in folder.iterdir()] == ["r.md"]
    finally:
        folder.chmod(0o755)


def _inode_after_report_write_in_sticky_directory(tmp_path, monkeypatch, as_other_user):
    import os

    from dokimi.commands._output import write_report_text

    folder = tmp_path / "sticky"
    folder.mkdir()
    folder.chmod(0o1777)
    target = folder / "r.md"
    target.write_text("old", encoding="utf-8")
    before = target.stat().st_ino
    if as_other_user:
        monkeypatch.setattr("os.geteuid", lambda: os.stat(target).st_uid + 1)
    write_report_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    return before, target.stat().st_ino


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="needs POSIX owners")
def test_write_report_text_rewrites_in_place_a_file_another_user_owns_in_a_sticky_directory(tmp_path, monkeypatch):
    before, after = _inode_after_report_write_in_sticky_directory(tmp_path, monkeypatch, as_other_user=True)
    assert after == before


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="needs POSIX owners")
def test_write_report_text_replaces_the_file_of_its_owner_in_a_sticky_directory(tmp_path, monkeypatch):
    before, after = _inode_after_report_write_in_sticky_directory(tmp_path, monkeypatch, as_other_user=False)
    assert after != before


@pytest.mark.skipif(not os.path.exists("/dev/null"), reason="needs /dev/null")
def test_write_report_text_writes_to_a_special_file_instead_of_a_tmp_beside_it():
    from pathlib import Path

    from dokimi.commands._output import write_report_text

    write_report_text(Path("/dev/null"), "text\n")
    assert Path("/dev/null").is_char_device()


def _checklist_report():
    from dokimi.eval.checklist import ChecklistReport, PointVerdict

    verdicts = {"q1": {"baseline": [PointVerdict("n\ud800", False, True)], "skill": [PointVerdict("n\ud800", True, False)]}}
    return ChecklistReport(judge="p/j", baseline_arm="baseline", verdicts=verdicts, questions={"q1": "q"})


def _pairwise_report():
    from dokimi.eval.pairwise import ArmPairwiseReport, PairVerdict

    return ArmPairwiseReport(judge="p/j", baseline_arm="baseline",
                             verdicts={"n\ud800": {"q1": PairVerdict(1, "A", "B")}})


def _cross_judge_report():
    from dokimi.eval.cross_judge import CrossJudgeReport, Measure

    scores = {("q1", "baseline"): 50.0, ("q1", "skill"): 60.0, ("q2", "baseline"): 50.0, ("q2", "skill"): 70.0}
    return CrossJudgeReport(baseline_arm="baseline", measures=[Measure(name="n\ud800", kind="aggregate", scores=scores)])


def _explore_markdown():
    from dokimi.report.exploration_report import render_explore_report

    return render_explore_report({"summary": {"per_arm": {"n\ud800": {"n": 1}}}})


@pytest.mark.parametrize("render", [
    lambda: _pairwise_report().format_markdown(),
    lambda: _checklist_report().format_markdown(),
    lambda: _cross_judge_report().format_markdown(),
    _explore_markdown,
], ids=["eval-pairwise", "eval-checklist", "eval-cross-judge", "explore"])
def test_write_text_or_exit_writes_a_report_with_a_lone_surrogate_as_a_question_mark(tmp_path, render):
    from dokimi.commands._output import write_text_or_exit

    target = tmp_path / "r.md"
    write_text_or_exit(target, render(), code=2)
    assert "n?" in target.read_text(encoding="utf-8")
