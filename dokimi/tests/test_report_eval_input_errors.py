# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""report eval: an unreadable --eval file is bad input (exit 1 with a message), not a traceback."""

from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize("content, reason", [
    (None, "cannot read"),
    (b"{not json", "is not valid UTF-8 JSON"),
    (b"\xff\xfe\x00", "is not valid UTF-8 JSON"),
    (b"[1, 2]", "`evaluations` list"),
    (b"{}", "`evaluations` list"),
    (json.dumps({"evaluations": {}}).encode(), "`evaluations` list"),
], ids=["missing", "malformed", "not-utf8", "not-an-object", "no-evaluations",
        "evaluations-not-a-list"])
def test_report_eval_exits_2_with_a_message_on_an_unreadable_eval_file(tmp_path, monkeypatch, capsys,
                                                                       content, reason):
    from dokimi import cli

    eval_file, out = tmp_path / "eval.json", tmp_path / "r.md"
    if content is not None:
        eval_file.write_bytes(content)
    monkeypatch.setattr("sys.argv", ["dokimi", "report", "eval", "--product", "P", "--eval", str(eval_file),
                                     "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(eval_file) in err and reason in err
    assert not out.exists()


def test_report_eval_exits_2_with_a_message_when_the_eval_path_is_a_directory(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    out = tmp_path / "r.md"
    monkeypatch.setattr("sys.argv", ["dokimi", "report", "eval", "--product", "P", "--eval", str(tmp_path),
                                     "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: cannot read ") and str(tmp_path) in err
    assert not out.exists()
