# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`evaluate`: a bad --custom-questions path or --output-dir exits 1 with a message."""

from __future__ import annotations

from tests.cli_refusal import refusal

from dokimi.cli import build_parser


def _evaluate(*extra: str):
    args = build_parser().parse_args(["evaluate", "--product", "p", "--description", "d", *extra])
    args.func(args)


def test_a_missing_custom_questions_file_exits_2_with_a_message(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with refusal() as out:
        _evaluate("--output-dir", str(tmp_path / "out"), "--custom-questions", str(missing))
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions path does not exist: {missing}" in err
    assert "Traceback" not in err


def test_an_unwritable_output_dir_exits_2_with_a_message(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with refusal() as out:
        _evaluate("--output-dir", str(blocker))
    assert out.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(blocker) in err
    assert "Traceback" not in err
