# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`tools preference --items-out`: a path that cannot be written exits 1 with a message."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.cli import build_parser

GROUPS = Path(__file__).resolve().parent / "fixtures" / "tool_preference" / "groups.json"


def _preference(items_out: Path) -> None:
    args = build_parser().parse_args([
        "tools", "preference", "--groups", str(GROUPS), "--dry-run", "--items-out", str(items_out),
    ])
    args.func(args)


def test_an_items_out_inside_a_regular_file_exits_1_naming_the_path(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "items.json"
    with pytest.raises(SystemExit) as exc:
        _preference(target)
    assert exc.value.code == 1
    assert f"Error: cannot write --items-out {target}" in capsys.readouterr().err


def test_an_items_out_that_is_a_directory_exits_1_naming_the_path(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _preference(tmp_path)
    assert exc.value.code == 1
    assert f"Error: cannot write --items-out {tmp_path}" in capsys.readouterr().err


def test_a_writable_items_out_still_gets_the_items(tmp_path, capsys):
    out = tmp_path / "items.json"
    _preference(out)
    assert out.read_text(encoding="utf-8").startswith("[")
    assert f"items -> {out}" in capsys.readouterr().out
