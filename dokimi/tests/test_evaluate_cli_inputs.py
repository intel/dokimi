# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`evaluate`: a bad --custom-questions path or --out-dir exits 1 with a message."""

from __future__ import annotations

import os

import pytest

from tests.cli_refusal import refusal

from dokimi.cli import build_parser


def _evaluate(*extra: str):
    args = build_parser().parse_args(["evaluate", "--product", "p", "--description", "d", *extra])
    args.func(args)


def test_a_missing_custom_questions_file_exits_2_with_a_message(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with refusal() as out:
        _evaluate("--out-dir", str(tmp_path / "out"), "--custom-questions", str(missing))
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions path does not exist: {missing}" in err
    assert "Traceback" not in err


def test_a_directory_as_custom_questions_exits_2_before_any_output_directory(tmp_path, capsys):
    out_dir = tmp_path / "out dir"
    with refusal() as out:
        _evaluate("--out-dir", str(out_dir), "--custom-questions", str(tmp_path))
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions {tmp_path}: not a file" in err
    assert "Traceback" not in err
    assert not out_dir.exists()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_an_unreadable_custom_questions_file_exits_2_before_any_output_directory(tmp_path, capsys):
    locked, out_dir = tmp_path / "q.json", tmp_path / "out"
    locked.write_text("[]", encoding="utf-8")
    locked.chmod(0)
    try:
        with refusal() as out:
            _evaluate("--out-dir", str(out_dir), "--custom-questions", str(locked))
    finally:
        locked.chmod(0o644)
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions {locked}: Permission denied" in err
    assert "Traceback" not in err
    assert not out_dir.exists()


def test_an_unwritable_output_dir_exits_2_with_a_message(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with refusal() as out:
        _evaluate("--out-dir", str(blocker))
    assert out.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(blocker) in err
    assert "Traceback" not in err
