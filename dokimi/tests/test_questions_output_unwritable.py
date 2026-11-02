# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions refine and review: an unwritable --output is a clean exit, not a traceback."""

from __future__ import annotations

import json

import pytest

from dokimi import cli

QUESTIONS = {"questions": [{"question": "How do I create a task group?", "difficulty": "beginner"}]}


def _write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


class _FakeReport:
    total = 0
    summary: dict = {}


class _FakeReviewer:
    def __init__(self, **_):
        pass

    def review_questions(self, questions, library_name, output_path=None, limit=None):
        _write(output_path)
        return _FakeReport()


@pytest.fixture
def questions_file(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps(QUESTIONS), encoding="utf-8")
    return path


@pytest.fixture
def out(tmp_path, request):
    if request.param == "under-a-file":
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        return blocker / "out.json"
    directory = tmp_path / "dir"
    directory.mkdir()
    return directory


def _run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize("out", ["under-a-file", "is-a-directory"], indirect=True)
def test_refine_exits_2_when_the_output_cannot_be_written(monkeypatch, capsys, questions_file, out):
    monkeypatch.setattr("dokimi.questions.artifact.save_questions_artifact",
                        lambda data, path: _write(path))
    assert _run(monkeypatch, "refine", "--product", "p", "--questions", str(questions_file),
                "--output", str(out)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err


@pytest.mark.parametrize("out", ["under-a-file", "is-a-directory"], indirect=True)
def test_panel_review_exits_2_when_the_output_cannot_be_written(monkeypatch, capsys, questions_file, out):
    monkeypatch.setattr("dokimi.questions.panel_reviewer.QuestionPanelReviewer", _FakeReviewer)
    assert _run(monkeypatch, "review", "--product", "p", "--questions", str(questions_file),
                "--output", str(out)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err
