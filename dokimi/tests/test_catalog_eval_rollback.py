# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The receipt swap of catalog eval: its rollback and a previous receipt that is a link."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
def test_a_rerun_replaces_a_linked_receipt_without_taking_its_targets_mode(tmp_path):
    from dokimi.commands.catalog_eval import _replace_receipts

    out, staged, backup = tmp_path / "out", tmp_path / "new", tmp_path / "old"
    for directory in (out, staged, backup):
        directory.mkdir()
    target = tmp_path / "target.md"
    target.write_text("target", encoding="utf-8")
    os.chmod(target, 0o600)
    (out / "report.md").symlink_to(target)
    (staged / "report.md").write_text("new", encoding="utf-8")
    os.chmod(staged / "report.md", 0o644)
    _replace_receipts(out, staged, backup)
    assert not (out / "report.md").is_symlink()
    assert (out / "report.md").read_text(encoding="utf-8") == "new"
    assert oct((out / "report.md").stat().st_mode & 0o7777) == "0o644"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_failed_swap_restores_receipts_when_the_unlink_of_a_read_only_one_is_refused(tmp_path, monkeypatch):
    from dokimi.commands.catalog_eval import _replace_receipts

    out, staged, backup = tmp_path / "out", tmp_path / "new", tmp_path / "old"
    for directory in (out, staged, backup):
        directory.mkdir()
    for name in ("catalog.json", "plan.json"):
        (out / name).write_text(f"old {name}", encoding="utf-8")
        (staged / name).write_text(f"new {name}", encoding="utf-8")
    os.chmod(out / "catalog.json", 0o444)
    real_replace, real_unlink = os.replace, Path.unlink
    failure = OSError("injected plan publication failure")

    def replace(src, dst):
        if Path(dst) == out / "plan.json" and Path(src).parent == staged:
            raise failure
        return real_replace(src, dst)

    def unlink(self, missing_ok=False):
        if self.exists() and not self.stat().st_mode & 0o200:  # Windows refuses a read-only file
            raise PermissionError(13, "read-only", str(self))
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr("dokimi.commands.catalog_eval.os.replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError) as raised:
        _replace_receipts(out, staged, backup)
    assert raised.value is failure
    assert {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()} == {
        "catalog.json": "old catalog.json", "plan.json": "old plan.json"}
    assert oct((out / "catalog.json").stat().st_mode & 0o7777) == "0o444"
