# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`explore list` / `explore run`: an unreadable or malformed task file exits with a message."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser

GOOD = "id: ok\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n"


def _exit_message(argv, capsys) -> str:
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    return capsys.readouterr().err


@pytest.mark.parametrize("argv_tail", [["list"], ["run", "--all"]])
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"id: [unclosed\n", "is not valid UTF-8 YAML"),
        (bytes([0xFF, 0xFE, 0x00]), "is not valid UTF-8 YAML"),
        pytest.param(b"[" * 100000, "is not valid UTF-8 YAML", id="too-deep"),
        (b"- just\n- a list\n", "must hold a YAML mapping"),
        (b"id: only-an-id\n", "missing required field(s)"),
        (b"id: x\nquery: q\nreferences: 5\n", "references must be a list"),
        (b"id: x\nquery: q\nreferences: [a.py]\n", "references must be a list"),
        (b"id: x\nquery: q\nreferences:\n  - lines: [1]\n", "references must be a list"),
        (b"id: x\nquery: q\nreferences:\n  - path: a.py\nrepo_root: 5\n", "repo_root must be"),
        pytest.param(b"id: x\nquery: q\nreferences:\n  - path: a.py\nrepo_root: 0\n", "repo_root must be",
                     id="falsy-root"),
        pytest.param(b"id: x\nquery: q\nreferences:\n  - path: a.py\nrepo_root: []\n", "repo_root must be",
                     id="empty-list-root"),
        pytest.param(b"id: x\ncreated: 2020-13-45\n", "is not valid UTF-8 YAML", id="bad-timestamp"),
        pytest.param(b"id: x\nquery: q\nreferences:\n  - path: a.py\n    lines: [0]\n", "values must be >= 1",
                     id="lines-zero"),
        pytest.param(b"id: x\nquery: q\nreferences:\n  - path: a.py\n    lines: [1, 2, 3]\n",
                     "must be [start, end] or [n]", id="lines-three"),
        pytest.param(b"id: x\nquery: q\nreferences:\n  - path: a.py\n    lines: [x]\n", "must contain integers",
                     id="lines-not-int"),
    ],
)
def test_a_malformed_task_file_names_the_file(tmp_path, argv_tail, content, message, capsys):
    (tmp_path / "ok.yaml").write_text(GOOD, encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_bytes(content)
    out = _exit_message(["explore", *argv_tail, "--tasks-root", str(tmp_path)], capsys)
    assert str(bad) in out
    assert message in out


def test_a_task_file_passed_by_path_names_the_file(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("references: {", encoding="utf-8")
    out = _exit_message(
        ["explore", "run", "--tasks", str(bad), "--tasks-root", str(tmp_path)], capsys
    )
    assert str(bad) in out
    assert "is not valid UTF-8 YAML" in out


def test_a_task_file_that_cannot_be_read_names_the_file(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    unreadable = tmp_path / "locked.yaml"
    unreadable.write_text(GOOD, encoding="utf-8")
    real = Path.read_text

    def deny(self, *args, **kwargs):
        if self == unreadable:
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny)
    out = _exit_message(["explore", "list", "--tasks-root", str(tmp_path)], capsys)
    assert f"Explore task {unreadable} cannot be read" in out


def test_selecting_a_task_by_id_fails_on_a_malformed_sibling_naming_it(tmp_path, capsys):
    (tmp_path / "ok.yaml").write_text(GOOD, encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: [unclosed\n", encoding="utf-8")  # it might declare the id asked for
    out = _exit_message(["explore", "run", "--tasks", "ok", "--tasks-root", str(tmp_path)], capsys)
    assert str(bad) in out
    assert "is not valid UTF-8 YAML" in out
