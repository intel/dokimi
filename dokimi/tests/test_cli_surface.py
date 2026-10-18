# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/cli_surface.py — the CLI-interface change report on PRs."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SCRIPT = PROJECT / "scripts" / "cli_surface.py"
_spec = importlib.util.spec_from_file_location("cli_surface", _SCRIPT)
assert _spec and _spec.loader
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _parser(extra: bool = False, default: int = 1, drop_cmd: bool = False) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dokimi")
    sub = p.add_subparsers(dest="cmd", required=True)
    if not drop_cmd:
        r = sub.add_parser("run")
        r.add_argument("questions")
        r.add_argument("--n", type=int, default=default)
        r.add_argument("--mode", choices=["a", "b"], default="a")
        if extra:
            r.add_argument("--dry-run", action="store_true")
    g = sub.add_parser("repo")
    gs = g.add_subparsers(dest="repo_cmd", required=True)
    gs.add_parser("scan").add_argument("--hidden", help=argparse.SUPPRESS)
    return p


def _surface(**kw: bool | int) -> dict:
    return cs._command(_parser(**kw), "dokimi")


def test_dump_of_the_real_cli_is_json_and_deterministic():
    first = json.dumps(cs.dump_surface(), sort_keys=True)
    assert first == json.dumps(cs.dump_surface(), sort_keys=True)
    assert "0x" not in first  # no object addresses leaking in from defaults or types
    assert "run" in json.loads(first)["subcommands"]


def test_nested_subcommands_and_hidden_options_are_walked():
    flat = cs._flatten(_surface(), {})
    assert set(flat) == {"dokimi", "dokimi run", "dokimi repo", "dokimi repo scan"}
    (hidden,) = flat["dokimi repo scan"]["params"]
    assert hidden["hidden"] is True


def test_identical_surfaces_have_no_diff():
    assert cs.diff_surfaces(_surface(), _surface()) == []


@pytest.mark.parametrize("base, head, expected", [
    ({}, {"extra": True}, "+ added option `--dry-run`"),
    ({"extra": True}, {}, "- removed option `--dry-run`"),
    ({}, {"default": 2}, "~ changed option `--n`: default: 1 -> 2"),
    ({}, {"drop_cmd": True}, "- removed command `dokimi run`"),
    ({"drop_cmd": True}, {}, "+ added command `dokimi run`"),
])
def test_each_kind_of_change_is_reported(base, head, expected):
    lines = cs.diff_surfaces(_surface(**base), _surface(**head))
    assert any(expected in line for line in lines), lines


def test_main_diff_exit_codes_and_markdown(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_surface()), encoding="utf-8")
    b.write_text(json.dumps(_surface(extra=True)), encoding="utf-8")
    assert cs.main(["diff", str(a), str(a)]) == 0
    assert "No user-facing CLI surface changes" in capsys.readouterr().out
    assert cs.main(["diff", str(a), str(b), "--markdown"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("## CLI interface change detected") and "```diff" in out


def test_main_dump_writes_file(tmp_path):
    out = tmp_path / "s.json"
    assert cs.main(["dump", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["path"] == "dokimi"
