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
    assert exc.value.code == 1


@pytest.mark.parametrize(
    ("subject_id", "products", "questions", "message"),
    [
        ("..", '["oneTBB"]', '["q.json"]', "subject.id must not resolve"),
        ("s", '["one/TBB", "one-TBB"]', '["q.json"]', "collide after output directory sanitization"),
        ("s", '["a", "b"]', '["q1.json", "q2.json", "q3.json"]', "suite.questions must contain either"),
    ],
)
def test_an_unrunnable_descriptor_exits_1_with_its_reason(
    tmp_path, monkeypatch, capsys, subject_id, products, questions, message
):
    _run(tmp_path, monkeypatch, subject_id=subject_id, products=products, questions=questions)

    assert message in capsys.readouterr().err.splitlines()[0]
    assert not (tmp_path / "results").exists()


def test_an_unknown_baseline_spec_exits_1_with_its_reason(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]', baseline="nosuchkind:x")

    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line.startswith("Error: ") and "nosuchkind" in first_line
    assert not (tmp_path / "results").exists()


def test_a_baseline_named_like_an_awareness_arm_exits_1_with_its_reason(tmp_path, monkeypatch, capsys):
    other = tmp_path / "other" / "docs"
    other.mkdir(parents=True)
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         baseline=f"docs:local:{other.as_posix()}")

    assert "duplicate runtime names" in capsys.readouterr().err.splitlines()[0]
    assert not (tmp_path / "results").exists()


def test_an_out_dir_that_is_a_file_exits_1_naming_it(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "out"
    blocker.write_text("x", encoding="utf-8")
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-dir", str(blocker)))

    first_line = capsys.readouterr().err.splitlines()[0]
    assert first_line.startswith(f"Error: cannot write {blocker}: ")
    assert blocker.read_text(encoding="utf-8") == "x"


def test_an_out_dir_under_a_file_exits_1_naming_it(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "out"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "sub"
    _run(tmp_path, monkeypatch, subject_id="s", products='["a"]', questions='["q.json"]',
         extra_args=("--out-dir", str(target)))

    assert capsys.readouterr().err.splitlines()[0].startswith(f"Error: cannot write {target}: ")


def test_an_unwritable_scorecard_exits_1_naming_it(tmp_path, monkeypatch, capsys):
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
