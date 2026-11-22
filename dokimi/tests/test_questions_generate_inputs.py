# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions generate: an unreadable --personas or --topics file is a clean exit, not a traceback."""

import json
from unittest.mock import Mock, patch

import pytest

from dokimi import cli

GOOD_PERSONAS = {"personas": [{"id": "p1", "name": "dev", "skill_level": "beginner"}]}


def _generate(monkeypatch, *extra):
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", "generate", "--product", "p", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '{"personas": "dev"}', "{}"],
                         ids=["missing", "malformed", "not-an-object", "personas-not-a-list", "no-personas"])
def test_generate_exits_2_on_an_unusable_personas_file(tmp_path, monkeypatch, capsys, content):
    bad = tmp_path / "personas.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(bad)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


@pytest.mark.parametrize(
    "personas",
    [[1, "x"], [{"id": "p1", "name": "dev"}], [{"name": "dev", "skill_level": "beginner"}],
     [{"id": "p1", "skill_level": "beginner"}]],
    ids=["items-not-objects", "missing-skill-level", "missing-id", "missing-name"])
def test_generate_exits_2_on_personas_missing_required_fields(tmp_path, monkeypatch, capsys, personas):
    bad = tmp_path / "personas.json"
    bad.write_text(json.dumps({"personas": personas}), encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(bad)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err and "skill_level" in err


@pytest.mark.parametrize("content", ["5", '{"a": 1}'], ids=["number", "object"])
def test_generate_exits_2_when_topics_is_not_a_list(tmp_path, monkeypatch, capsys, content):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    bad = tmp_path / "topics.json"
    bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(bad)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err and "list" in err


@pytest.mark.parametrize("content", [None, "{not json"], ids=["missing", "malformed"])
def test_generate_exits_2_on_an_unreadable_topics_file(tmp_path, monkeypatch, capsys, content):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    bad = tmp_path / "topics.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(bad)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


class _FakeGenerator:
    def __init__(self, **_):
        pass

    def generate_questions(self, **_):
        return [{"question": "q?"}]

    def save_questions(self, questions, output_path, product=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("[]", encoding="utf-8")


def _unwritable_outputs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    directory = tmp_path / "dir"
    directory.mkdir()
    return [blocker / "out.json", directory]


@pytest.mark.parametrize("which", [0, 1], ids=["under-a-file", "is-a-directory"])
def test_generate_exits_2_when_the_output_cannot_be_written(tmp_path, monkeypatch, capsys, which):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    topics = tmp_path / "topics.json"
    topics.write_text("[]", encoding="utf-8")
    out = _unwritable_outputs(tmp_path)[which]
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    monkeypatch.setattr("dokimi.questions.QuestionGenerator", _FakeGenerator)
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(topics),
                     "--output", str(out)) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot write ") and str(out) in err


@pytest.mark.parametrize("config_text", ["retrieval:\n", "products:\n"], ids=["empty-retrieval", "empty-products"])
def test_generate_completes_when_a_config_section_is_empty(tmp_path, monkeypatch, capsys, config_text):
    config = tmp_path / "products.yaml"
    config.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr("dokimi.config.runtime_config_path", lambda: config)
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    topics = tmp_path / "topics.json"
    topics.write_text("[]", encoding="utf-8")
    out = tmp_path / "q.json"
    monkeypatch.setattr("dokimi.questions.QuestionGenerator", _FakeGenerator)
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", "generate", "--product", "p",
                                     "--personas", str(personas), "--topics", str(topics),
                                     "--output", str(out)])
    cli.main()
    assert out.exists()
    assert "Saved 1 questions" in capsys.readouterr().out


class _BogusGenerator(_FakeGenerator):
    model = "m"
    provider = "p"
    _get_timestamp = staticmethod(lambda: "2026-01-01T00:00:00Z")

    def generate_questions(self, **_):
        return [{"bogus": 1}]

    def save_questions(self, questions, output_path, product=None):
        from dokimi.questions.llm_gen import QuestionGenerator
        QuestionGenerator.save_questions(self, questions, output_path, product)


def test_generate_exits_1_when_the_generated_set_fails_validation(tmp_path, monkeypatch, capsys):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    topics = tmp_path / "topics.json"
    topics.write_text("[]", encoding="utf-8")
    out = tmp_path / "o.json"
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    monkeypatch.setattr("dokimi.questions.QuestionGenerator", _BogusGenerator)
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(topics),
                     "--output", str(out)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "Traceback" not in err
    assert not out.exists()


def test_generate_records_the_product_so_runs_list_finds_the_set(tmp_path, monkeypatch, capsys):
    def runs_list(subject):
        monkeypatch.setattr("sys.argv", ["dokimi", "runs", "list", "--subject", subject, "--json"])
        cli.main()
        return json.loads(capsys.readouterr().out)["data"]["artifacts"]

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    (tmp_path / "personas.json").write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    (tmp_path / "topics.json").write_text('["parallel_for"]', encoding="utf-8")
    llm = Mock(invoke=Mock(return_value=Mock(content='["How does parallel_for split a range?"]')))
    monkeypatch.setattr("sys.argv", [
        "dokimi", "eval", "questions", "generate", "--product", "oneTBB", "--personas", "personas.json",
        "--topics", "topics.json", "--model", "gen-m", "--output", "results/r1/questions/oneTBB.json"])
    with patch("dokimi.questions.llm_gen.Invoker", Mock(return_value=llm)):
        cli.main()
    capsys.readouterr()

    assert json.loads((tmp_path / "results/r1/questions/oneTBB.json").read_text())["product"] == "oneTBB"
    (found,) = runs_list("oneTBB")
    assert (found["kind"], found["subject"], found["model"], found["path"]) == (
        "questions", "oneTBB", "gen-m", "r1/questions/oneTBB.json")
    assert runs_list("oneDAL") == []


@pytest.mark.parametrize("scheme", ["local", "okf"])
def test_generate_exits_2_when_a_local_doc_source_path_is_missing(tmp_path, monkeypatch, capsys, scheme):
    """#1086: `--doc-source local:<missing>` and `okf:<missing>` are input errors naming the path, before any generation."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    (tmp_path / "personas.json").write_text('{"personas": []}', encoding="utf-8")
    missing = tmp_path / "missing"
    assert _generate(monkeypatch, "--personas", "personas.json", "--doc-source", f"{scheme}:{missing}",
                     "--output", "out.json") == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(missing) in err
    assert not (tmp_path / "out.json").exists()


def test_generate_accepts_an_existing_local_doc_source(tmp_path, monkeypatch, capsys):
    """#1086: the path check passes for an existing directory."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("# parallel_for\nUse tbb::parallel_for.", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    (tmp_path / "personas.json").write_text('{"personas": []}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", "generate", "--product", "oneTBB",
                                     "--personas", "personas.json", "--doc-source", f"local:{docs}",
                                     "--output", "out.json"])
    cli.main()
    assert "Error" not in capsys.readouterr().err


def test_generate_exits_2_when_a_local_doc_source_name_is_too_long(tmp_path, monkeypatch, capsys):
    """#1086: a filename past NAME_MAX is a missing path, an input error and not a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dokimi.commands.answers._load_runtime_config", dict)
    (tmp_path / "personas.json").write_text('{"personas": []}', encoding="utf-8")
    assert _generate(monkeypatch, "--personas", "personas.json", "--doc-source", f"local:{'0' * 256}",
                     "--output", "out.json") == 2
    assert capsys.readouterr().err.startswith("Error: ")


def test_require_local_doc_source_reports_a_permission_error_as_unreadable(monkeypatch):
    """#1086: `local:/restricted/docs` under a directory denying search exists; it is not `path does not exist`."""
    from pathlib import Path

    from dokimi.commands.questions import _require_local_doc_source
    from dokimi.utils import InputError

    def denied(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "exists", denied)
    with pytest.raises(InputError, match="cannot read /restricted/docs") as exc:
        _require_local_doc_source("local:/restricted/docs")
    assert "does not exist" not in str(exc.value)


def test_require_local_doc_source_reports_a_denied_directory_when_exists_answers_false(tmp_path, monkeypatch):
    """#1086: Python 3.14 `Path.exists` returns False on a denied directory; the guard still says unreadable."""
    import os
    from pathlib import Path

    from dokimi.commands.questions import _require_local_doc_source
    from dokimi.utils import InputError

    restricted = tmp_path / "restricted"
    (restricted / "docs").mkdir(parents=True)
    restricted.chmod(0)
    if os.access(restricted / "docs", os.F_OK):
        restricted.chmod(0o700)
        pytest.skip("directory permissions are not enforced for this user")
    monkeypatch.setattr(Path, "exists", lambda self: os.path.exists(self))
    try:
        with pytest.raises(InputError, match="cannot read") as exc:
            _require_local_doc_source(f"local:{restricted / 'docs'}")
    finally:
        restricted.chmod(0o700)
    assert "does not exist" not in str(exc.value)
