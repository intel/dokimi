# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`evaluate`: a bad --custom-questions path or --output-dir exits 1 with a message."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser


def _evaluate(*extra: str):
    args = build_parser().parse_args(["evaluate", "--product", "p", "--description", "d", *extra])
    args.func(args)


def test_a_missing_custom_questions_file_exits_1_with_a_message(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit) as exc:
        _evaluate("--output-dir", str(tmp_path / "out"), "--custom-questions", str(missing))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"Error: --custom-questions path does not exist: {missing}" in err
    assert "Traceback" not in err


def test_an_unwritable_output_dir_exits_1_with_a_message(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _evaluate("--output-dir", str(blocker))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error: " in err and "Not a directory" in err
    assert "Traceback" not in err
