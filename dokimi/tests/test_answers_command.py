# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``answers generate``: the config, question and output-path helpers, and the command over them.

The Answerer and the documentation-source client are replaced with fakes, so the command runs
its whole body without a model or a network.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import dokimi.eval
import dokimi.mcp.factory
import dokimi.questions.artifact
from dokimi.commands import answers
from dokimi.defaults import DEFAULT_RETRIEVAL_TOP_K


def _args(**overrides) -> argparse.Namespace:
    base = dict(product="Lib", questions="q.json", top_k=None, model="m", provider="p",
                rerank_threshold=0.5, debug_retrieval=False, max_tokens=100, concurrency=1,
                output=None, run_id=None, doc_source="context7", context7_id=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def _point_config(monkeypatch, path: Path) -> None:
    monkeypatch.setattr("dokimi.config.runtime_config_path", lambda: path)


def test_missing_config_is_empty(tmp_path, monkeypatch):
    _point_config(monkeypatch, tmp_path / "absent.yaml")
    assert answers._load_runtime_config() == {}


def test_empty_config_file_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "products.yaml"
    path.write_text("", encoding="utf-8")
    _point_config(monkeypatch, path)
    assert answers._load_runtime_config() == {}


def test_config_file_is_read(tmp_path, monkeypatch):
    path = tmp_path / "products.yaml"
    path.write_text("retrieval:\n  top_k: 7\n", encoding="utf-8")
    _point_config(monkeypatch, path)
    assert answers._load_runtime_config() == {"retrieval": {"top_k": 7}}


def test_invalid_yaml_config_exits(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_text("products: [unclosed\n", encoding="utf-8")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert "invalid YAML" in capsys.readouterr().err


def test_non_utf8_config_exits_with_message(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_bytes(b"retrieval:\n  top_k: \xff\xfe\n")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert f"cannot read {path}" in capsys.readouterr().err


def test_unreadable_config_exits_with_message(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.mkdir()  # a directory where the file should be: read_text raises OSError
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert f"cannot read {path}" in capsys.readouterr().err


def test_non_mapping_config_exits(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert "expected a mapping" in capsys.readouterr().err


def test_non_utf8_config_exits(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_bytes(b"\xff\xfe\x00bad: [")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert f"cannot read {path}" in capsys.readouterr().err


def test_unreadable_config_path_exits(tmp_path, monkeypatch, capsys):
    _point_config(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert f"cannot read {tmp_path}" in capsys.readouterr().err


@pytest.mark.parametrize(("text", "key"), [
    ("retrieval: 5", "retrieval"), ("products: [1]", "products"),
    ('retrieval: ""', "retrieval"), ("products: []", "products"),
])
def test_config_section_of_the_wrong_shape_exits(tmp_path, monkeypatch, capsys, text, key):
    path = tmp_path / "products.yaml"
    path.write_text(text, encoding="utf-8")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 2
    assert f"'{key}' in {path} must be a mapping" in capsys.readouterr().err


def test_questions_load_error_exits(monkeypatch, capsys):
    def fail(path):
        raise ValueError("record 3 has no question text")

    monkeypatch.setattr(dokimi.questions.artifact, "load_questions", fail)
    with pytest.raises(SystemExit) as exc:
        answers._load_questions("q.json")
    assert exc.value.code == 2
    assert "record 3 has no question text" in capsys.readouterr().err


def test_output_path_precedence():
    assert answers._output_path(_args(output="x/out.json")) == Path("x/out.json")
    assert answers._output_path(_args(run_id="r1")) == Path("results/lib_r1/answers/Lib.json")
    assert answers._output_path(_args()) == Path("answers/Lib.json")


@pytest.mark.parametrize("top_k, config_text, expected_top_k", [
    (5, "retrieval:\n  top_k: 7\n", 5),             # the flag wins over the config and the default
    (None, "retrieval:\n  top_k: 7\n", 7),          # else the config's retrieval.top_k
    (None, None, DEFAULT_RETRIEVAL_TOP_K),          # else the built-in default
    (None, "retrieval:\n", DEFAULT_RETRIEVAL_TOP_K),  # an empty section is the default
    (None, "products:\n", DEFAULT_RETRIEVAL_TOP_K),   # an empty products section does not matter
])
def test_generate_writes_answers_to_output_path(tmp_path, monkeypatch, capsys,
                                                top_k, config_text, expected_top_k):
    if config_text is None:
        _point_config(monkeypatch, tmp_path / "absent.yaml")
    else:
        path = tmp_path / "products.yaml"
        path.write_text(config_text, encoding="utf-8")
        _point_config(monkeypatch, path)
    monkeypatch.setattr(dokimi.questions.artifact, "load_questions",
                        lambda path: SimpleNamespace(questions=["q1", "q2"]))
    monkeypatch.setattr(dokimi.mcp.factory, "create_doc_source_client", lambda source: "client")
    monkeypatch.setattr(dokimi.mcp.factory, "resolve_library_id_for_product",
                        lambda product, client, explicit_id=None: "/lib/id")
    calls: dict = {}

    class FakeAnswerer:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def generate_answers(self, **kwargs):
            calls["generate"] = kwargs
            return ["a1"]

        def save_answers(self, answers_, path):
            calls["saved"] = (answers_, path)

    monkeypatch.setattr(dokimi.eval, "Answerer", FakeAnswerer)
    out = tmp_path / "out.json"

    answers.cmd_answers_generate(_args(output=str(out), top_k=top_k))

    assert calls["init"]["top_k"] == expected_top_k
    assert calls["generate"]["questions"] == ["q1", "q2"]
    assert calls["generate"]["library_id"] == "/lib/id"
    assert calls["generate"]["output_path"] == out
    assert calls["saved"] == (["a1"], out)
    assert f"Saved answers to {out}" in capsys.readouterr().out


def test_doc_source_none_asks_the_baseline_without_a_doc_client(tmp_path, monkeypatch, capsys):
    _point_config(monkeypatch, tmp_path / "absent.yaml")
    monkeypatch.setattr(dokimi.questions.artifact, "load_questions",
                        lambda path: SimpleNamespace(questions=["q1"]))

    def no_client(*args, **kwargs):
        raise AssertionError("--doc-source none must not build a documentation client")

    monkeypatch.setattr(dokimi.mcp.factory, "create_doc_source_client", no_client)
    monkeypatch.setattr(dokimi.mcp.factory, "resolve_library_id_for_product", no_client)
    calls: dict = {}

    class FakeAnswerer:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def generate_answers(self, **kwargs):
            return ["a1"]

        def save_answers(self, answers_, path):
            calls["saved"] = path

    monkeypatch.setattr(dokimi.eval, "Answerer", FakeAnswerer)

    answers.cmd_answers_generate(_args(output=str(tmp_path / "out.json"), doc_source="none"))

    assert calls["init"]["mcp_client"] is None
    assert "Documentation source: none" in capsys.readouterr().out


def _stub_generate(monkeypatch, tmp_path, answerer_cls):
    _point_config(monkeypatch, tmp_path / "absent.yaml")
    monkeypatch.setattr(dokimi.questions.artifact, "load_questions",
                        lambda path: SimpleNamespace(questions=["q1"]))
    monkeypatch.setattr(dokimi.mcp.factory, "create_doc_source_client", lambda source: "client")
    monkeypatch.setattr(dokimi.mcp.factory, "resolve_library_id_for_product",
                        lambda product, client, explicit_id=None: "/lib/id")
    monkeypatch.setattr(dokimi.eval, "Answerer", answerer_cls)


def test_generate_output_that_cannot_be_written_exits_2_naming_the_path(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    target = blocker / "a.json"

    class FakeAnswerer:
        def __init__(self, **kwargs):
            pass

        def generate_answers(self, output_path, **kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            return ["a1"]

    _stub_generate(monkeypatch, tmp_path, FakeAnswerer)
    with pytest.raises(SystemExit) as exc:
        answers.cmd_answers_generate(_args(output=str(target)))
    assert exc.value.code == 2
    assert f"Error: cannot write {target}: " in capsys.readouterr().err


def test_generate_final_save_that_fails_exits_2_naming_the_path(tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.json"

    class FakeAnswerer:
        def __init__(self, **kwargs):
            pass

        def generate_answers(self, **kwargs):
            return ["a1"]

        def save_answers(self, answers_, path):
            raise PermissionError(13, "Permission denied", f"{path}.123.0.tmp")

    _stub_generate(monkeypatch, tmp_path, FakeAnswerer)
    with pytest.raises(SystemExit) as exc:
        answers.cmd_answers_generate(_args(output=str(target)))
    assert exc.value.code == 2
    assert f"Error: cannot write {target}: Permission denied" in capsys.readouterr().err


def test_generate_output_that_is_a_directory_exits_2_naming_the_path(tmp_path, monkeypatch, capsys):
    from dokimi.eval import Answerer as RealAnswerer

    target = tmp_path / "outdir"
    target.mkdir()

    class FakeAnswerer:
        def __init__(self, **kwargs):
            pass

        def generate_answers(self, **kwargs):
            return [{"question_id": "q1"}]

        model = "m"
        provider = "p"

        def _get_timestamp(self):
            return "t"

        _build_output = RealAnswerer._build_output

        save_answers = RealAnswerer.save_answers

    _stub_generate(monkeypatch, tmp_path, FakeAnswerer)
    with pytest.raises(SystemExit) as exc:
        answers.cmd_answers_generate(_args(output=str(target)))
    assert exc.value.code == 2
    assert f"Error: cannot write {target}: " in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_generate_unreadable_questions_exits_2_naming_the_path(tmp_path, monkeypatch, capsys, kind):
    _point_config(monkeypatch, tmp_path / "absent.yaml")
    path = tmp_path / "q.json"
    if kind == "directory":
        path.mkdir()
    with pytest.raises(SystemExit) as exc:
        answers.cmd_answers_generate(_args(questions=str(path)))
    assert exc.value.code == 2
    assert f"Error: cannot read {path}" in capsys.readouterr().err
