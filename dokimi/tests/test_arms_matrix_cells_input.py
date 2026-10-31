# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`arms run` / `arms matrix-run`: an unreadable --matrix-cells file exits 1 with a message."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser


def _questions(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [{"id": "q1", "question": "What is x?"}]}), encoding="utf-8")
    return path


def _run(tmp_path, cells, capsys, *argv):
    args = build_parser().parse_args([
        "arms", *argv, "--product", "p", "--questions", str(_questions(tmp_path)),
        "--arms", "baseline", "--matrix-cells", str(cells),
        *(["--matrix-cell", "c1"] if argv[0] == "run" else ["--out-dir", str(tmp_path / "out")]),
    ])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "matrix-run"])
def test_a_directory_as_matrix_cells_is_reported_as_unreadable(tmp_path, capsys, command):
    err = _run(tmp_path, tmp_path, capsys, command)
    assert f"cannot read {tmp_path}" in err


@pytest.mark.parametrize("command", ["run", "matrix-run"])
@pytest.mark.parametrize("content", ["{not json", "[" * 100000], ids=["malformed", "too-deep"])
def test_matrix_cells_that_are_not_json_name_the_file(tmp_path, capsys, command, content):
    bad = tmp_path / "cells.json"
    bad.write_text(content, encoding="utf-8")
    assert f"{bad} is not valid JSON" in _run(tmp_path, bad, capsys, command)
