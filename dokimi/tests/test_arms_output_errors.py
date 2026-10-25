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
