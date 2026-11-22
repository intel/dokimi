# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Report writers publish a name with a lone surrogate (from JSON, YAML or argv) as ``?`` and join a pair, not a UnicodeEncodeError."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.orchestrator.pipeline import EvaluationPipeline
from dokimi.report.markdown_report import write_compare_report, write_run_report

NAMES = pytest.mark.parametrize(
    ("name", "written"),
    [("n\ud800", "n?"), ("n" + chr(0xD83D) + chr(0xDE00), "n\U0001F600")],
    ids=["lone-surrogate", "surrogate-pair"],
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@NAMES
def test_run_report_writes_the_name_of_a_doc_path(tmp_path, name, written):
    run = {
        "summary": {"docs": 1, "score": 0.5, "coverage": 0.5, "freshness_lite": 0.5, "readability": 0.5},
        "docs": [{"path": name, "score": 0.5, "coverage": 0.5, "freshness_lite": 0.5, "readability": 0.5, "chunks": 2}],
    }
    out = tmp_path / "run.md"
    write_run_report(run, out)
    assert f"- {written}: score=0.5000 cov=0.5000 fresh=0.5000 read=0.5000 chunks=2\n" in _text(out)


@NAMES
def test_compare_report_writes_the_name_of_a_metric(tmp_path, name, written):
    data = {
        "diff": {"docs": 0, "score": 0.0, "coverage": 0.0, "freshness_lite": 0.0, "readability": 0.0},
        "regressions": {
            "has_critical": False, "has_warnings": False,
            "score": {"severity": "OK", "delta": 0.0},
            "metrics": [{"metric": name, "severity": "OK", "delta": 0.0}],
        },
    }
    out = tmp_path / "compare.md"
    write_compare_report(data, out)
    assert f"✅ **{written}:** +0.0000 (OK)\n" in _text(out)


_EVALUATION = {
    "question_id": "q1", "with_docs": {"aggregate": 80}, "without_docs": {"aggregate": 60}, "delta": 20,
}


@NAMES
def test_report_eval_writes_the_question_text(tmp_path, monkeypatch, name, written):
    monkeypatch.setattr("dokimi.report.eval_report._write_qa_json", lambda path, payload: None)  # the sidecar is save_artifact's: #991
    ev, out = tmp_path / "eval.json", tmp_path / "r.md"
    ev.write_text(json.dumps({"evaluations": [{**_EVALUATION, "question_text": f"Q {name}"}]}), encoding="utf-8")
    args = build_parser().parse_args(["report", "eval", "--product", "P", "--eval", str(ev), "--out", str(out)])
    args.func(args)
    report = _text(out)
    assert f"Q {written}" in report


@NAMES
def test_report_eval_of_a_knowledge_run_writes_the_question_text(tmp_path, monkeypatch, name, written):
    from tests.test_single_condition_arms_report import _arms_eval

    monkeypatch.setattr("dokimi.report.single_condition_report._write_qa", lambda *a, **k: None)  # the sidecar: #991
    data = _arms_eval({"q1": 80.0})
    data["evaluations"][0]["question_text"] = f"Q {name}"
    ev, out = tmp_path / "eval.json", tmp_path / "r.md"
    ev.write_text(json.dumps(data), encoding="utf-8")
    args = build_parser().parse_args(["report", "eval", "--product", "P", "--eval", str(ev), "--out", str(out)])
    args.func(args)
    report = _text(out)
    assert "(knowledge)" in report and f"Q {written}" in report


@NAMES
def test_model_compare_writes_the_run_id(tmp_path, name, written):
    run = tmp_path / "run.json"
    run.write_text(json.dumps({"baseline_arm": "baseline", "evaluations": [
        {"question_id": f"q{i}", "question_text": "q", "category": "", "difficulty": "easy",
         "persona": "", "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 60.0}}}
        for i in range(3)
    ]}), encoding="utf-8")
    out = tmp_path / "mc.md"
    args = build_parser().parse_args(
        ["report", "model-compare", "--regular-runs", str(run), "--run-ids", name, "--out", str(out)])
    args.func(args)
    assert written in _text(out)


@NAMES
def test_pipeline_report_step_writes_the_question_text(tmp_path, name, written):
    output_dir = tmp_path / "run"
    pipeline = EvaluationPipeline(product="oneTBB", output_dir=output_dir, description="Threading Building Blocks")
    text = f"Q {name}"
    pipeline.eval_path.write_text(
        json.dumps({"evaluations": [{**_EVALUATION, "question_text": text}]}), encoding="utf-8")
    pipeline._generate_report([], [{"id": "q1", "question": text}])
    assert f"Q {written}" in _text(pipeline.report_path)


def _stub_scorecard_run(monkeypatch, text):
    import dokimi.artifacts
    import dokimi.commands.arms
    import dokimi.report.subject_report
    import dokimi.subjects

    monkeypatch.setattr(dokimi.commands.arms, "run_matrix", lambda config, **kw: None)
    monkeypatch.setattr(dokimi.artifacts, "load_artifact", lambda kind, path: {})
    monkeypatch.setattr(dokimi.artifacts, "save_artifact", lambda kind, data, path: None)
    monkeypatch.setattr(dokimi.subjects, "build_subject_scorecard", lambda *a, **k: {})
    monkeypatch.setattr(dokimi.report.subject_report, "render_subject_scorecard", lambda scorecard: text)
    monkeypatch.setattr("dokimi.commands.subjects._run_subject_work", lambda *a, **k: {})


@NAMES
def test_subjects_run_writes_the_scorecard_report(tmp_path, monkeypatch, name, written):
    _stub_scorecard_run(monkeypatch, f"# {name}\n")
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        'baseline = "baseline"\n[subject]\nid = "s"\nkind = "doc-source"\nref = "local:docs"\n'
        '[suite]\nproducts = ["a"]\nquestions = ["q.json"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    md = tmp_path / "card.md"
    args = build_parser().parse_args(
        ["subjects", "run", str(descriptor), "--out", str(tmp_path / "card.json"), "--out-md", str(md)])
    args.func(args)
    assert _text(md) == f"# {written}\n"


@NAMES
def test_subjects_work_writes_the_task_runs_report(tmp_path, monkeypatch, name, written):
    from types import SimpleNamespace

    import dokimi.artifacts
    import dokimi.harnesses
    import dokimi.report.task_runs_report
    from dokimi.commands.subjects import _run_subject_work

    class FakeRunner:
        def __init__(self, **_):
            pass

        def validate_harnesses(self):
            pass

        def run(self):
            return {"summary": {"per_harness": {name: {"n": 1}}}, "results": []}

    monkeypatch.setattr(dokimi.harnesses, "TaskSuiteRunner", FakeRunner)
    monkeypatch.setattr(dokimi.artifacts, "save_artifact", lambda kind, data, path: None)
    monkeypatch.setattr("dokimi.commands.subjects._work_arm_specs", lambda descriptor: [])
    work_dir = tmp_path / "work"
    args = SimpleNamespace(skip_work=False, work_harnesses="h", work_baseline_harness=None,
                           work_output_dir=str(work_dir), work_model=None, model="m",
                           work_command_template=None, work_dry_run=False, work_repeats=1)
    _run_subject_work(SimpleNamespace(suite=SimpleNamespace(tasks=["t"])), args, out_dir=tmp_path / "out", tasks=[object()])
    assert written in _text(work_dir / "task-runs.md")


@NAMES
def test_tasks_run_writes_the_task_runs_report(tmp_path, monkeypatch, name, written):
    import dokimi.report.task_runs_report

    task = tmp_path / "toy-task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Do it.", encoding="utf-8")
    (task / "task.toml").write_text("[metadata]\ntags = [\"toy\"]\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("exit 0\n", encoding="utf-8")
    monkeypatch.setattr(dokimi.report.task_runs_report, "render_task_runs_report", lambda output: f"# {name}\n")
    md = tmp_path / "nested" / "report.md"
    args = build_parser().parse_args([
        "tasks", "run", "--tasks", "toy-task", "--tasks-root", str(tmp_path), "--harnesses", "codex",
        "--model", "m", "--out", str(tmp_path / "runs.json"), "--out-md", str(md), "--dry-run",
    ])
    args.func(args)
    assert _text(md) == f"# {written}\n"
