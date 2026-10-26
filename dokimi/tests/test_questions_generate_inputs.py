# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions generate: an unreadable --personas or --topics file is a clean exit, not a traceback."""

import json

import pytest

from dokimi import cli

GOOD_PERSONAS = {"personas": [{"id": "p1", "name": "dev", "skill_level": "beginner"}]}


def _generate(monkeypatch, *extra):
    monkeypatch.setattr("sys.argv", ["dokimi", "questions", "generate", "--product", "p", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '{"personas": "dev"}', "{}"],
                         ids=["missing", "malformed", "not-an-object", "personas-not-a-list", "no-personas"])
def test_generate_exits_1_on_an_unusable_personas_file(tmp_path, monkeypatch, capsys, content):
    bad = tmp_path / "personas.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


@pytest.mark.parametrize("personas", [[1, "x"], [{"id": "p1", "name": "dev"}]],
                         ids=["items-not-objects", "missing-skill-level"])
def test_generate_exits_1_on_personas_missing_required_fields(tmp_path, monkeypatch, capsys, personas):
    bad = tmp_path / "personas.json"
    bad.write_text(json.dumps({"personas": personas}), encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err and "skill_level" in err


@pytest.mark.parametrize("content", ["5", '{"a": 1}'], ids=["number", "object"])
def test_generate_exits_1_when_topics_is_not_a_list(tmp_path, monkeypatch, capsys, content):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    bad = tmp_path / "topics.json"
    bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err and "list" in err


@pytest.mark.parametrize("content", [None, "{not json"], ids=["missing", "malformed"])
def test_generate_exits_1_on_an_unreadable_topics_file(tmp_path, monkeypatch, capsys, content):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    bad = tmp_path / "topics.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


class _FakeGenerator:
    def __init__(self, **_):
        pass

    def generate_questions(self, **_):
        return [{"question": "q?"}]

    def save_questions(self, questions, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("[]", encoding="utf-8")


def _unwritable_outputs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    directory = tmp_path / "dir"
    directory.mkdir()
    return [blocker / "out.json", directory]


@pytest.mark.parametrize("which", [0, 1], ids=["under-a-file", "is-a-directory"])
def test_generate_exits_1_when_the_output_cannot_be_written(tmp_path, monkeypatch, capsys, which):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    topics = tmp_path / "topics.json"
    topics.write_text("[]", encoding="utf-8")
    out = _unwritable_outputs(tmp_path)[which]
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    monkeypatch.setattr("dokimi.questions.QuestionGenerator", _FakeGenerator)
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(topics),
                     "--output", str(out)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err
