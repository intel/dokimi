# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The --out-json pre-flight of `tools preference`: refused before any probe call, or accepted."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

GROUPS = Path(__file__).resolve().parent / "fixtures" / "tool_preference" / "groups.json"


@pytest.mark.parametrize("blocked", ["file-as-parent", "directory"])
def test_cli_refuses_an_unwritable_out_json_before_any_call(monkeypatch, tmp_path, capsys, blocked):
    import dokimi.llm as llm
    from dokimi.cli import build_parser

    calls = []

    def fake(*a, **k):
        calls.append(a)
        raise RuntimeError("must not be reached")

    monkeypatch.setattr(llm, "chat_completion", fake)
    if blocked == "file-as-parent":
        (tmp_path / "blocker").write_text("x", encoding="utf-8")
        out = tmp_path / "blocker" / "pref.json"
    else:
        out = tmp_path / "pref.json"
        out.mkdir()
    args = build_parser().parse_args(["tools", "preference", "--groups", str(GROUPS), "--model", "m",
                                      "--reps", "1", "--factors", "assertive", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert calls == []
    err = capsys.readouterr().err
    assert f"cannot write {out}: " in err
    if blocked == "directory":
        assert "Is a directory" in err


def test_out_json_check_creates_missing_parents_and_leaves_no_file(tmp_path):
    from dokimi.commands.tools import _check_out_json_writable

    out = tmp_path / "a" / "b" / "pref.json"
    _check_out_json_writable(str(out))
    assert out.parent.is_dir()
    assert not out.exists()


@pytest.mark.skipif(os.name == "nt", reason="os.replace refuses a read-only destination on Windows, "
                                             "which the POSIX rule pinned here does not model")
def test_out_json_check_accepts_a_read_only_file_in_a_writable_dir(tmp_path):
    from dokimi.commands.tools import _check_out_json_writable

    out = tmp_path / "pref.json"
    out.write_text("{}", encoding="utf-8")
    out.chmod(0o444)
    try:
        if os.access(out, os.W_OK):
            pytest.skip("chmod 0444 does not block writes here (for example, running as root)")
        _check_out_json_writable(str(out))  # the save replaces the file through its directory
    finally:
        out.chmod(0o644)


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:  # Windows needs Developer Mode or admin rights to create a symlink
        pytest.skip(f"cannot create a symlink here: {exc}")


def test_out_json_check_leaves_no_file_at_a_dangling_symlink_target(tmp_path):
    from dokimi.commands.tools import _check_out_json_writable

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link.json"
    _symlink_or_skip(link, real / "t.json")
    _check_out_json_writable(str(link))
    assert not (real / "t.json").exists()
    assert link.is_symlink()


def test_out_json_check_refuses_a_dangling_symlink_into_a_missing_dir(tmp_path, capsys):
    from dokimi.commands.tools import _check_out_json_writable

    link = tmp_path / "link.json"
    _symlink_or_skip(link, tmp_path / "missing" / "t.json")
    with pytest.raises(SystemExit) as exc:
        _check_out_json_writable(str(link))
    assert exc.value.code == 2
    assert "cannot write" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="chmod 0555 does not make a directory read-only on Windows")
def test_out_json_check_refuses_a_writable_file_in_a_read_only_dir(tmp_path):
    from dokimi.commands.tools import _check_out_json_writable

    ro = tmp_path / "ro"
    ro.mkdir()
    out = ro / "pref.json"
    out.write_text("{}", encoding="utf-8")
    ro.chmod(0o555)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("chmod 0555 does not block writes here (for example, running as root)")
        with pytest.raises(SystemExit) as exc:
            _check_out_json_writable(str(out))
        assert exc.value.code == 2
    finally:
        ro.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="chmod 0555 does not make a directory read-only on Windows")
def test_out_json_check_probes_the_symlink_target_directory(tmp_path):
    from dokimi.commands.tools import _check_out_json_writable

    ro = tmp_path / "ro"
    ro.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    link = out_dir / "link.json"
    _symlink_or_skip(link, ro / "t.json")
    ro.chmod(0o555)
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("chmod 0555 does not block writes here (for example, running as root)")
        with pytest.raises(SystemExit) as exc:
            _check_out_json_writable(str(link))
        assert exc.value.code == 2
    finally:
        ro.chmod(0o755)


def test_out_json_check_refuses_a_symlink_to_a_directory(tmp_path, capsys):
    from dokimi.commands.tools import _check_out_json_writable

    real = tmp_path / "dir"
    real.mkdir()
    link = tmp_path / "link.json"
    _symlink_or_skip(link, real)
    with pytest.raises(SystemExit) as exc:
        _check_out_json_writable(str(link))
    assert exc.value.code == 2
    assert "Is a directory" in capsys.readouterr().err
