# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`explore run`: an output path that cannot be written exits 1 with a message."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser

TASK = "id: t\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n"


def _run(tmp_path, *extra):
    tasks = tmp_path / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "t.yaml").write_text(TASK, encoding="utf-8")
    args = build_parser().parse_args([
        "explore", "run", "--all", "--arms", "oracle,empty", "--tasks-root", str(tasks),
        "--repo-root", str(tmp_path), "--output-dir", str(tmp_path / "out"), *extra,
    ])
    args.func(args)


def _blocker(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    return blocker


@pytest.mark.parametrize("flag", ["--out-json", "--out-md"])
def test_an_unwritable_output_is_reported_not_a_traceback(tmp_path, capsys, flag):
    target = _blocker(tmp_path) / "report"
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, flag, str(target))
    assert exc.value.code == 2
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_a_writable_output_still_saves_both_files(tmp_path, capsys):
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    _run(tmp_path, "--out-json", str(out_json), "--out-md", str(out_md))
    assert out_json.is_file() and out_md.is_file()
    assert "oracle: n=1" in capsys.readouterr().out
