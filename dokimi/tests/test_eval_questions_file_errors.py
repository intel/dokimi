# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval checklist / cross-judge --grounded: an unreadable --questions file is bad input (exit 2)."""

from __future__ import annotations

import json

import pytest


def _arms_file(tmp_path):
    path = tmp_path / "arms.json"
    path.write_text(json.dumps({"answers": [{"question_id": "q1", "arms": {"skill": {"answer": "s"}}}]}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("command", [
    ["checklist"],
    ["cross-judge", "--grounded", "--no-stored"],
], ids=["checklist", "cross-judge-grounded"])
@pytest.mark.parametrize("content", [None, "{not json", "\xff\xfe"],
                         ids=["missing", "malformed", "not-utf8"])
def test_an_unreadable_questions_file_exits_2_with_a_message(tmp_path, monkeypatch, capsys, command, content):
    from dokimi import cli

    questions = tmp_path / "questions.json"
    if content == "\xff\xfe":
        questions.write_bytes(b"\xff\xfe\x00")
    elif content is not None:
        questions.write_text(content, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", *command, "--answers", str(_arms_file(tmp_path)),
                                     "--questions", str(questions), "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # bad input; 1 is the verdict code of the gates
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(questions) in err
    assert not (tmp_path / "out.json").exists()
