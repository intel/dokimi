# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Three writers keep the mode and group of the file they replace (#1165): they publish through ``dokimi.atomic_write``."""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from pathlib import Path

import pytest

from dokimi.atomic_write import write_text_atomic
from dokimi.mcp.utils import write_cache_text
from dokimi.repos import batch_ledger

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and groups")


def _group_file(path: Path, text: str = "old") -> int:
    """A file of mode 0o640 in a group the user belongs to besides the primary one; the group's id."""
    other = [g for g in os.getgroups() if g != os.getegid()]
    if not other:
        pytest.skip("the user has no supplementary group")
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o640)  # after creation, so the umask does not hide a loss
    os.chown(path, -1, other[0])
    return other[0]


def _ground_args(atoms: Path, out: Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(atoms=str(atoms), out=None if out is None else str(out), tool=["perf report"],
                              tool_version=None, header=None, shared_lib=None, prefix=None)


def _atoms_file(path: Path, monkeypatch) -> None:
    from dokimi.novelty import grounding
    from dokimi.novelty.atoms import Atom, Span, save_atoms

    span = Span(id="s1", kind="prose", text="perf report shows samples", start=0, end=25)
    save_atoms([Atom(statement="perf report shows samples", question="How?",
                     answer="perf report --stdio", span_ids=("s1",))], [span], path)
    monkeypatch.setattr(grounding, "help_text", lambda tool, **k: "Usage: perf report\n  --stdio  text output\n")


def test_a_cache_write_keeps_the_mode_and_group_of_the_entry_it_replaces(tmp_path):
    entry = tmp_path / "k.txt"
    group = _group_file(entry)

    assert write_cache_text(entry, "new") is True

    assert entry.read_text(encoding="utf-8") == "new"
    assert (entry.stat().st_mode & 0o7777, entry.stat().st_gid) == (0o640, group)


def test_a_ledger_write_keeps_the_mode_and_group_of_the_file_it_replaces(tmp_path):
    ledger = tmp_path / "spend.json"
    group = _group_file(ledger)

    batch_ledger.write_json(ledger, {"b": 1, "a": [2]})

    assert ledger.read_bytes() == b'{\n "a": [\n  2\n ],\n "b": 1\n}\n'
    assert (ledger.stat().st_mode & 0o7777, ledger.stat().st_gid) == (0o640, group)


def test_a_ledger_file_that_did_not_exist_is_private_and_a_cache_entry_is_not(tmp_path):
    old_umask = os.umask(0o002)
    try:
        batch_ledger.write_json(tmp_path / "spend.json", {"x": 1})
        write_cache_text(tmp_path / "k.txt", "new")
    finally:
        os.umask(old_umask)

    assert (tmp_path / "spend.json").stat().st_mode & 0o7777 == 0o600
    assert (tmp_path / "k.txt").stat().st_mode & 0o7777 == 0o664


def test_a_ledger_write_replaces_a_link_at_its_path_and_leaves_the_target(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text("untouched", encoding="utf-8")
    ledger = tmp_path / "spend.json"
    ledger.symlink_to(target)

    batch_ledger.write_json(ledger, {"spent_usd": 1})

    assert not ledger.is_symlink() and json.loads(ledger.read_text(encoding="utf-8")) == {"spent_usd": 1}
    assert target.read_text(encoding="utf-8") == "untouched"


def test_a_ledger_write_replaces_a_dangling_link_and_a_directory(tmp_path):
    dangling = tmp_path / "a.json"
    dangling.symlink_to(tmp_path / "missing.json")
    batch_ledger.write_json(dangling, {"x": 1})
    assert not dangling.is_symlink() and json.loads(dangling.read_text(encoding="utf-8")) == {"x": 1}
    (tmp_path / "b.json").mkdir()
    (tmp_path / "b.json" / "inner").write_text("x", encoding="utf-8")
    batch_ledger.write_json(tmp_path / "b.json", {"y": 2})
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == {"y": 2}


def test_a_ledger_write_replaces_a_link_that_loops_back_to_itself(tmp_path):
    ledger = tmp_path / "result.json"
    ledger.symlink_to("result.json")

    batch_ledger.write_json(ledger, {"x": 1})

    assert not ledger.is_symlink() and json.loads(ledger.read_text(encoding="utf-8")) == {"x": 1}


def test_an_atomic_write_whose_stat_fails_for_another_reason_raises_and_keeps_the_file(tmp_path, monkeypatch):
    target = tmp_path / "spend.json"
    target.write_text("old", encoding="utf-8")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == target:
            raise OSError(errno.EIO, "Input/output error")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(OSError) as raised:
        write_text_atomic(target, "new")

    assert raised.value.errno == errno.EIO
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["spend.json"]


def test_a_failed_ledger_write_leaves_the_old_file_and_no_tmp(tmp_path, monkeypatch):
    ledger = tmp_path / "spend.json"
    ledger.write_text("old", encoding="utf-8")

    def refuse(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        batch_ledger.write_json(ledger, {"x": 1})
    assert ledger.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["spend.json"]


def test_novelty_ground_keeps_the_mode_and_group_of_the_atoms_file(tmp_path, monkeypatch):
    from dokimi.commands import novelty_ground

    atoms = tmp_path / "atoms.json"
    _atoms_file(atoms, monkeypatch)
    os.chmod(atoms, 0o640)
    other = [g for g in os.getgroups() if g != os.getegid()]
    if not other:
        pytest.skip("the user has no supplementary group")
    os.chown(atoms, -1, other[0])

    novelty_ground.cmd_novelty_ground(_ground_args(atoms))

    written = atoms.read_text(encoding="utf-8")
    assert "identifiers" in written
    assert written == json.dumps(json.loads(written), indent=2)
    assert (atoms.stat().st_mode & 0o7777, atoms.stat().st_gid) == (0o640, other[0])
