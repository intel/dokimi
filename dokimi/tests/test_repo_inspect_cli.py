# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``repo inspect`` command line: artifact, JSON and exit codes."""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import load_artifact, validate_artifact
from dokimi.cli import build_parser
from tests.test_repo_environment import _repo


@pytest.mark.outcome("cjm-repo-inspect")
def test_cli_writes_the_artifact(tmp_path, capsys):
    root = tmp_path / "toy"
    _repo(root, {"src/a.py": "x = 1\n"})
    out = tmp_path / "env.json"
    args = build_parser().parse_args(["repo", "inspect", str(root), "--out", str(out), "--fail-on-error"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    data = load_artifact("repo_environment", out)
    assert data["schema_version"] == "repo_environment.v1"
    text = capsys.readouterr().out
    assert "ERROR checks.test.missing" in text and "verify blocked" in text


@pytest.mark.outcome("cjm-repo-inspect")
def test_cli_json_prints_the_stamped_payload(tmp_path, capsys):
    _repo(tmp_path, {"AGENTS.md": "x"})
    args = build_parser().parse_args(["repo", "inspect", str(tmp_path), "--json"])
    args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == "repo_environment.v1"
    validate_artifact("repo_environment", data)


@pytest.mark.outcome("cjm-repo-inspect")
@pytest.mark.parametrize("case", ["missing source", "unwritable out"])
def test_cli_exits_2_when_it_cannot_read_or_write(tmp_path, capsys, case):
    _repo(tmp_path / "toy", {"AGENTS.md": "x"})
    (tmp_path / "file").write_text("x")
    argv = ["repo", "inspect", str(tmp_path / "toy"), "--out", str(tmp_path / "file" / "env.json")]
    if case == "missing source":
        argv = ["repo", "inspect", str(tmp_path / "nope")]
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert "✗" in capsys.readouterr().err
