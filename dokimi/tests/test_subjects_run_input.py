# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`subjects run`: a descriptor that cannot be run exits 1 with a message, before writing anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.cli import build_parser


def _run(tmp_path: Path, monkeypatch, *, subject_id: str, products: str, questions: str, baseline: str = "baseline",
         extra_args: tuple[str, ...] = ()):
    descriptor = tmp_path / "subject.toml"
    descriptor.write_text(
        f"""
baseline = "{baseline}"

[subject]
id = "{subject_id}"
kind = "doc-source"
ref = "local:docs"

[suite]
products = {products}
questions = {questions}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["subjects", "run", str(descriptor), *extra_args])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("subject_id", "products", "questions", "message"),
    [
        ("..", '["oneTBB"]', '["q.json"]', "subject.id must not resolve"),
        ("s", '["one/TBB", "one-TBB"]', '["q.json"]', "collide after output directory sanitization"),
        ("s", '["a", "b"]', '["q1.json", "q2.json", "q3.json"]', "suite.questions must contain either"),
    ],
)
def test_an_unrunnable_descriptor_exits_2_with_its_reason(
    tmp_path, monkeypatch, capsys, subject_id, products, questions, message
):
    _run(tmp_path, monkeypatch, subject_id=subject_id, products=products, questions=questions)

    assert message in capsys.readouterr().err.splitlines()[0]
    assert not (tmp_path / "results").exists()


def test_an_unknown_baseline_spec_exits_2_with_its_reason(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]', baseline="nosuchkind:x")

    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line.startswith("Error: ") and "nosuchkind" in first_line
    assert not (tmp_path / "results").exists()


def test_a_baseline_named_like_an_awareness_arm_exits_2_with_its_reason(tmp_path, monkeypatch, capsys):
    other = tmp_path / "other" / "docs"
    other.mkdir(parents=True)
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         baseline=f"docs:local:{other.as_posix()}")

    assert "duplicate runtime names" in capsys.readouterr().err.splitlines()[0]
    assert not (tmp_path / "results").exists()


def test_an_out_dir_that_is_a_file_exits_2_naming_it(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "out"
    blocker.write_text("x", encoding="utf-8")
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-dir", str(blocker)))

    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line.startswith(f"Error: cannot write {blocker}: ")
    assert blocker.read_text(encoding="utf-8") == "x"


def test_an_out_dir_under_a_file_exits_2_naming_it(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "out"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "sub"
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-dir", str(target)))

    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def test_an_unwritable_scorecard_exits_2_naming_it(tmp_path, monkeypatch, capsys):
    import dokimi.artifacts
    import dokimi.commands.arms
    import dokimi.subjects

    monkeypatch.setattr(dokimi.commands.arms, "cmd_arms_matrix_run", lambda args: None)
    monkeypatch.setattr(dokimi.artifacts, "load_artifact", lambda kind, path: {})
    monkeypatch.setattr(dokimi.subjects, "build_subject_scorecard", lambda *a, **k: {})
    monkeypatch.setattr("dokimi.commands.subjects._run_subject_work", lambda *a, **k: {})
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "scorecard.json"

    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-json", str(target)))

    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def _stub_run(monkeypatch, saved):
    import dokimi.artifacts
    import dokimi.commands.arms
    import dokimi.report.subject_report
    import dokimi.subjects

    monkeypatch.setattr(dokimi.commands.arms, "cmd_arms_matrix_run", lambda args: None)
    monkeypatch.setattr(dokimi.artifacts, "load_artifact", lambda kind, path: {})
    monkeypatch.setattr(dokimi.artifacts, "save_artifact", lambda kind, data, path: saved.append(path))
    monkeypatch.setattr(dokimi.subjects, "build_subject_scorecard", lambda *a, **k: {})
    monkeypatch.setattr(dokimi.report.subject_report, "render_subject_scorecard", lambda scorecard: "md")
    monkeypatch.setattr("dokimi.commands.subjects._run_subject_work", lambda *a, **k: {})


def test_an_unwritable_report_dir_exits_2_after_the_scorecard_is_saved(tmp_path, monkeypatch, capsys):
    saved: list = []
    _stub_run(monkeypatch, saved)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "sub" / "x.md"

    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-json", str(tmp_path / "card.json"), "--out-md", str(target)))

    assert saved == [tmp_path / "card.json"]
    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def test_a_report_path_that_is_a_directory_exits_2_naming_it(tmp_path, monkeypatch, capsys):
    saved: list = []
    _stub_run(monkeypatch, saved)
    target = tmp_path / "report.md"
    target.mkdir()

    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-json", str(tmp_path / "card.json"), "--out-md", str(target)))

    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def _work(tmp_path, monkeypatch, work_dir: Path, save):
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
            return {}

    monkeypatch.setattr(dokimi.harnesses, "TaskSuiteRunner", FakeRunner)
    monkeypatch.setattr(dokimi.artifacts, "save_artifact", save)
    monkeypatch.setattr(dokimi.report.task_runs_report, "render_task_runs_report", lambda artifact: "md")
    descriptor = SimpleNamespace(suite=SimpleNamespace(tasks=["t"]))
    args = SimpleNamespace(skip_work=False, work_harnesses="h", work_baseline_harness=None,
                           work_output_dir=str(work_dir), work_model=None, model="m",
                           work_command_template=None, work_dry_run=False, work_repeats=1)
    with pytest.raises(SystemExit) as exc:
        _run_subject_work(descriptor, args, out_dir=tmp_path / "out", tasks=[object()])
    assert exc.value.code == 2


def test_work_exits_2_when_the_task_runs_report_dir_cannot_be_created(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    saved: list = []

    _work(tmp_path, monkeypatch, blocker / "work", lambda kind, data, path: saved.append(path))

    assert saved == [blocker / "work" / "task-runs.json"]
    expected = blocker / "work" / "task-runs.md"
    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {expected}: ")


def test_work_exits_2_when_the_task_runs_json_cannot_be_written(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "work" / "task-runs.json"

    def save(kind, data, path):
        path.parent.mkdir(parents=True, exist_ok=True)

    _work(tmp_path, monkeypatch, blocker / "work", save)

    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def test_work_exits_2_when_the_task_runs_report_path_is_a_directory(tmp_path, monkeypatch, capsys):
    work_dir = tmp_path / "work"
    (work_dir / "task-runs.md").mkdir(parents=True)

    _work(tmp_path, monkeypatch, work_dir, lambda kind, data, path: None)

    expected = work_dir / "task-runs.md"
    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {expected}: ")
