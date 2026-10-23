# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`report generate`: an unreadable or invalid eval file exits 1 with a message."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser


def _generate(tmp_path, eval_path):
    questions = tmp_path / "questions.json"
    questions.write_text('{"questions": []}', encoding="utf-8")
    args = build_parser().parse_args([
        "report", "generate", "--product", "p", "--eval", str(eval_path),
        "--questions", str(questions), "--output", str(tmp_path / "r.md"),
    ])
    args.func(args)


def _err(tmp_path, eval_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _generate(tmp_path, eval_path)
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_a_missing_eval_file_is_reported_as_unreadable(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    assert f"Error: cannot read {missing}" in _err(tmp_path, missing, capsys)


def test_a_directory_as_eval_is_reported_as_unreadable(tmp_path, capsys):
    assert f"Error: cannot read {tmp_path}" in _err(tmp_path, tmp_path, capsys)


@pytest.mark.parametrize("text", ["", "{not json"])
def test_an_eval_file_that_is_not_json_is_reported_as_invalid_json(tmp_path, capsys, text):
    bad = tmp_path / "bad.json"
    bad.write_text(text, encoding="utf-8")
    assert f"Error: {bad} is not valid JSON" in _err(tmp_path, bad, capsys)


def test_a_non_utf8_eval_file_is_reported_as_invalid_utf8_json(tmp_path, capsys):
    binary = tmp_path / "bin.json"
    binary.write_bytes(bytes([0xFF, 0xFE]))
    assert f"Error: {binary} is not valid JSON" in _err(tmp_path, binary, capsys)


@pytest.mark.parametrize("text", ["[1, 2]", "null", "5", '"x"'])
def test_eval_json_that_is_not_an_object_is_rejected(tmp_path, capsys, text):
    bad = tmp_path / "notobj.json"
    bad.write_text(text, encoding="utf-8")
    assert f"Error: {bad} must hold a JSON object" in _err(tmp_path, bad, capsys)


@pytest.mark.parametrize("evaluations", ["null", "5"])
def test_non_list_evaluations_exit_1_with_its_path(tmp_path, capsys, evaluations):
    bad = tmp_path / "evd.json"
    bad.write_text(f'{{"evaluations": {evaluations}}}', encoding="utf-8")
    assert f"{bad} evaluations: expected a list of records" in _err(tmp_path, bad, capsys)
