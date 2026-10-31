# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty view`: an unreadable or invalid --novelty or --atoms file exits 2 with a message naming it."""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from dokimi.cli import build_parser
from tests.test_novelty_atoms_input import _WRONG_SHAPES


def _view(novelty, atoms, out):
    args = build_parser().parse_args([
        "novelty", "view", "--novelty", str(novelty), "--atoms", str(atoms),
        "--format", "md", "--out", str(out),
    ])
    args.func(args)


def _err(tmp_path, novelty, atoms, capsys):
    """Run `novelty view` through cli.main, as a user does, and return what it printed."""
    from dokimi import cli

    out = tmp_path / "annotated.md"
    argv = ["dokimi", "novelty", "view", "--novelty", str(novelty), "--atoms", str(atoms),
            "--format", "md", "--out", str(out)]
    with mock.patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert not out.exists()
    return capsys.readouterr().err


def test_a_missing_novelty_file_is_reported_as_unreadable(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    assert f"cannot read {missing}" in _err(tmp_path, missing, tmp_path / "a.json", capsys)


def test_a_directory_as_novelty_is_reported_as_unreadable(tmp_path, capsys):
    assert f"cannot read {tmp_path}" in _err(tmp_path, tmp_path, tmp_path / "a.json", capsys)


@pytest.mark.parametrize("payload", [b"", b"{not json", bytes([0xFF, 0xFE]), b"[1, 2]", b"{}", pytest.param(b"[" * 100000 + b"]" * 100000, id="nested_deeply")])
def test_a_novelty_file_that_is_not_a_novelty_artifact_is_reported_as_invalid(tmp_path, capsys, payload):
    bad = tmp_path / "bad.json"
    bad.write_bytes(payload)
    assert f"{bad} is not " in _err(tmp_path, bad, tmp_path / "a.json", capsys)


def test_a_missing_atoms_file_is_reported_as_unreadable(tmp_path, capsys):
    from dokimi.artifacts import save_artifact
    from tests.test_novelty_cli import _minimal_artifact

    novelty = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty)
    missing = tmp_path / "missing-atoms.json"
    assert f"cannot read {missing}" in _err(tmp_path, novelty, missing, capsys)


@pytest.mark.parametrize("payload", [
    b"", b"{not json", bytes([0xFF, 0xFE]), b"[1, 2]", pytest.param(b"[" * 100000 + b"]" * 100000, id="nested_deeply"),
    b'{"atoms": [{"unexpected": 1}]}', b'{"atoms": "x"}', b'{"spans": [1]}',
])
def test_an_atoms_file_that_is_not_an_atoms_record_is_reported_as_invalid(tmp_path, capsys, payload):
    from dokimi.artifacts import save_artifact
    from tests.test_novelty_cli import _minimal_artifact

    novelty = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty)
    bad = tmp_path / "atoms.json"
    bad.write_bytes(payload)
    assert f"{bad} is not " in _err(tmp_path, novelty, bad, capsys)


def test_an_atoms_error_names_the_exception_without_its_repr_quotes(tmp_path, capsys):
    from dokimi.artifacts import save_artifact
    from tests.test_novelty_cli import _minimal_artifact

    novelty = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty)
    bad = tmp_path / "atoms.json"
    bad.write_text('{"atoms": [{"unexpected": 1}]}', encoding="utf-8")
    assert f"{bad} is not " in _err(tmp_path, novelty, bad, capsys)


@pytest.mark.parametrize("kind", [k for k in _WRONG_SHAPES if not k.startswith("bundle")])
def test_an_atoms_file_of_a_wrong_shape_is_reported_as_invalid_by_view(tmp_path, capsys, kind):
    from dokimi.artifacts import save_artifact
    from tests.test_novelty_cli import _minimal_artifact

    novelty = tmp_path / "novelty.json"
    save_artifact("novelty", _minimal_artifact(), novelty)
    bad = tmp_path / "atoms.json"
    bad.write_text(_WRONG_SHAPES[kind], encoding="utf-8")
    assert f"{bad} is not " in _err(tmp_path, novelty, bad, capsys)
