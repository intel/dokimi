# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval personas validate` rejects a persona file it cannot validate with a message, not a traceback."""

from __future__ import annotations

import json

import pytest

from dokimi import cli

_PERSONA = {"id": "p1", "name": "Ana", "skill_level": "novice"}


def _approve(path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "personas", "validate", "--file", str(path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_approve_accepts_a_valid_persona_file(tmp_path, monkeypatch, capsys):
    f = tmp_path / "personas.json"
    f.write_text(json.dumps({"product": "oneTBB", "personas": [_PERSONA]}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "personas", "validate", "--file", str(f)])
    cli.main()
    assert "Validated 1 personas" in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    [
        b"[1, 2]",
        b'{"product": "p", "personas": 3}',
        b'{"product": "p", "personas": ["not-an-object"]}',
        b"\xff\xfe\x00",
    ],
    ids=["not-an-object", "personas-not-a-list", "persona-not-an-object", "not-utf8"],
)
def test_approve_exits_2_with_a_message_on_an_invalid_file(tmp_path, monkeypatch, capsys, content):
    f = tmp_path / "personas.json"
    f.write_bytes(content)
    assert _approve(f, monkeypatch) == 2
    err = capsys.readouterr().err
    assert err.startswith("✗ ") and "Traceback" not in err
    if content.startswith(b"\xff"):
        assert f"Cannot parse {f}" in err
    if content.endswith(b'["not-an-object"]}'):
        assert "'not-an-object'" in err


def test_approve_exits_2_when_the_path_is_a_directory(tmp_path, monkeypatch, capsys):
    assert _approve(tmp_path, monkeypatch) == 2
    assert f"Cannot read {tmp_path}" in capsys.readouterr().err
