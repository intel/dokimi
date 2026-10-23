# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`questions analyze` and `questions panel-review` exit 1 with a message on a bad questions file."""

from __future__ import annotations

import argparse

import pytest

from dokimi.commands.questions import cmd_questions_analyze, cmd_questions_panel_review


def _args(path, tmp_path):
    return argparse.Namespace(
        questions=str(path), product="lib", output=str(tmp_path / "out.json"), model="m",
        provider="openai", reviewers=None, concurrency=1, limit=None,
    )


def _bad_files(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    return {"invalid json": invalid, "not utf-8": binary, "a directory": tmp_path,
            "missing": tmp_path / "missing.json", "nested too deep": deep}


@pytest.mark.parametrize("cmd", [cmd_questions_analyze, cmd_questions_panel_review])
@pytest.mark.parametrize("case", ["invalid json", "not utf-8", "a directory", "missing", "nested too deep"])
def test_unreadable_questions_file_exits_1_with_a_message(cmd, case, tmp_path, capsys):
    path = _bad_files(tmp_path)[case]

    with pytest.raises(SystemExit) as exc:
        cmd(_args(path, tmp_path))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ")
    assert str(path) in err
    assert "Traceback" not in err
    assert not (tmp_path / "out.json").exists()
