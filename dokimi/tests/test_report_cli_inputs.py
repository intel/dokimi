# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`report generate`: an unreadable or malformed eval file exits 1 with a message."""

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


def test_a_missing_eval_file_exits_1_with_its_path(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit) as exc:
        _generate(tmp_path, missing)
    assert exc.value.code == 1
    assert str(missing) in capsys.readouterr().err


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_a_malformed_eval_file_exits_1_with_its_path(tmp_path, capsys, text):
    bad = tmp_path / "bad.json"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _generate(tmp_path, bad)
    assert exc.value.code == 1
    assert str(bad) in capsys.readouterr().err


def test_a_non_utf8_eval_file_exits_1_with_its_path(tmp_path, capsys):
    binary = tmp_path / "bin.json"
    binary.write_bytes(bytes([0xFF, 0xFE]))
    with pytest.raises(SystemExit) as exc:
        _generate(tmp_path, binary)
    assert exc.value.code == 1
    assert str(binary) in capsys.readouterr().err
