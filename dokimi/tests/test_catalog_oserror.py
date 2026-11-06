# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An OS error on one skill's files is that skill's error row, not a crash of the catalog scan.

The sibling is simulated: its ``SKILL.md`` is read, or its path stat-ed, through a monkeypatched
``Path`` method that raises ``ENAMETOOLONG``, the error a name over the filesystem limit gives.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from dokimi.external import read_skill_manifest, review_skills


def _skill(root: Path, name: str, description: str = "Use for things.") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n", encoding="utf-8")


def _too_long(path: Path) -> OSError:
    return OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG), str(path))


def test_load_error_on_a_sibling_is_an_error_row(tmp_path, monkeypatch):
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.parent.name == "sib":
            raise _too_long(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    entries = {e.name: e for e in read_skill_manifest(tmp_path)}

    assert entries["good"].loaded
    assert not entries["sib"].loaded
    assert "File name too long" in entries["sib"].error


@pytest.mark.parametrize("method", ["read_text", "read_bytes"])
def test_read_error_on_a_loaded_skill_is_an_error_review(tmp_path, monkeypatch, method):
    _skill(tmp_path, "good")
    _skill(tmp_path, "sib")
    entries = read_skill_manifest(tmp_path)  # both load; the failure comes after
    real = getattr(Path, method)

    def fail_for_sib(self, *args, **kwargs):
        if self.parent.name == "sib":
            raise _too_long(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, fail_for_sib)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["good"].state == "ok"
    assert reviews["sib"].state == "error"
    assert [f.code for f in reviews["sib"].findings] == ["unreadable"]


def test_stat_error_on_an_unloaded_sibling_is_no_review_and_no_crash(tmp_path, monkeypatch):
    _skill(tmp_path, "good")
    _skill(tmp_path, "bad", description="")  # no description: does not load
    real_is_dir = Path.is_dir

    def is_dir(self):
        if self.name == "bad":
            raise _too_long(self)
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    entries = read_skill_manifest(tmp_path)
    reviews = dict(zip((e.name for e in entries), review_skills(entries), strict=True))

    assert reviews["bad"] is None
    assert reviews["good"].state == "ok"
