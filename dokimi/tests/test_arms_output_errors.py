# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`arms plugin-delta`: an unwritable output exits 1 with a message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser

from test_plugin_delta import _arms_artifact


def _plugin_delta(tmp_path: Path, *out: str) -> None:
    baseline = tmp_path / "baseline.json"
    plugin = tmp_path / "plugin.json"
    baseline.write_text(json.dumps(_arms_artifact()), encoding="utf-8")
    plugin.write_text(json.dumps(_arms_artifact("caveman:full")), encoding="utf-8")
    args = build_parser().parse_args([
        "arms", "plugin-delta", "--baseline-json", str(baseline), "--plugin-json", str(plugin), *out,
    ])
    args.func(args)


def test_unwritable_out_json_exits_1_naming_the_path(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    target = blocker / "delta.json"
    with pytest.raises(SystemExit) as exc:
        _plugin_delta(tmp_path, "--out-json", str(target))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"Error: cannot write {target}" in err
    assert "Traceback" not in err


def test_unwritable_out_md_exits_1_naming_the_path(tmp_path, capsys):
    out_md = tmp_path / "is-a-dir"
    out_md.mkdir()
    with pytest.raises(SystemExit) as exc:
        _plugin_delta(tmp_path, "--out-json", str(tmp_path / "delta.json"), "--out-md", str(out_md))
    assert exc.value.code == 1
    assert f"Error: cannot write {out_md}" in capsys.readouterr().err


def _args(*argv: str):
    return build_parser().parse_args(["arms", *argv])


def _blocker(tmp_path: Path) -> Path:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    return blocker


def test_save_arms_output_exits_1_naming_an_unwritable_json(tmp_path, capsys):
    from dokimi.commands.arms import _save_arms_output

    target = _blocker(tmp_path) / "arms.json"
    with pytest.raises(SystemExit) as exc:
        _save_arms_output({}, target, tmp_path / "arms.md")
    assert exc.value.code == 1
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_save_arms_output_exits_1_naming_an_unwritable_report(tmp_path, capsys):
    from dokimi.commands.arms import _save_arms_output

    out_md = tmp_path / "is-a-dir"
    out_md.mkdir()
    output = _arms_artifact()
    with pytest.raises(SystemExit) as exc:
        _save_arms_output(output, tmp_path / "arms.json", out_md)
    assert exc.value.code == 1
    assert f"Error: cannot write {out_md}" in capsys.readouterr().err


def test_matrix_run_exits_1_naming_an_uncreatable_out_dir(tmp_path, capsys):
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [{"name": "c1", "model": "m", "provider": "anthropic", "harness": "arms-runner"}]}), encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "What is x?"}]}), encoding="utf-8")
    out_dir = _blocker(tmp_path) / "out"
    args = _args("matrix-run", "--product", "p", "--questions", str(questions), "--arms", "baseline",
                 "--matrix-cells", str(cells), "--out-dir", str(out_dir))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"Error: cannot write {out_dir / 'cells'}" in err and "Traceback" not in err
