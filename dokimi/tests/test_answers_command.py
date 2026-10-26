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
    assert exc.value.code == 1
    assert "invalid YAML" in capsys.readouterr().err


def test_non_mapping_config_exits(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 1
    assert "expected a mapping" in capsys.readouterr().err


def test_non_utf8_config_exits(tmp_path, monkeypatch, capsys):
    path = tmp_path / "products.yaml"
    path.write_bytes(b"\xff\xfe\x00bad: [")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 1
    assert f"cannot read {path}" in capsys.readouterr().err


def test_unreadable_config_path_exits(tmp_path, monkeypatch, capsys):
    _point_config(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 1
    assert f"cannot read {tmp_path}" in capsys.readouterr().err


@pytest.mark.parametrize(("text", "key"), [("retrieval: 5", "retrieval"), ("products: [1]", "products")])
def test_config_section_of_the_wrong_shape_exits(tmp_path, monkeypatch, capsys, text, key):
    path = tmp_path / "products.yaml"
    path.write_text(text, encoding="utf-8")
    _point_config(monkeypatch, path)
    with pytest.raises(SystemExit) as exc:
        answers._load_runtime_config()
    assert exc.value.code == 1
    assert f"'{key}' in {path} must be a mapping" in capsys.readouterr().err


def test_questions_load_error_exits(monkeypatch, capsys):
    def fail(path):
        raise ValueError("record 3 has no question text")

    monkeypatch.setattr(dokimi.questions.artifact, "load_questions", fail)
    with pytest.raises(SystemExit) as exc:
        answers._load_questions("q.json")
    assert exc.value.code == 1
    assert "record 3 has no question text" in capsys.readouterr().err


def test_output_path_precedence():
    assert answers._output_path(_args(output="x/out.json")) == Path("x/out.json")
    assert answers._output_path(_args(run_id="r1")) == Path("results/lib_r1/answers/Lib.json")
    assert answers._output_path(_args()) == Path("answers/Lib.json")


@pytest.mark.parametrize("top_k, config_text, expected_top_k", [
    (5, "retrieval:\n  top_k: 7\n", 5),             # the flag wins over the config and the default
    (None, "retrieval:\n  top_k: 7\n", 7),          # else the config's retrieval.top_k
    (None, None, DEFAULT_RETRIEVAL_TOP_K),          # else the built-in default
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
