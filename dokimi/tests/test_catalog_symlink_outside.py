# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A skills.yaml entry whose SKILL.md links out of the root is refused before the file is opened (#1126)."""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path

import pytest

from dokimi.external import read_skill_manifest

_SKILL = "---\nname: {name}\ndescription: Use for {name} things.\n---\n# {name}\n"


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:  # Windows without Developer Mode or the symlink privilege
        pytest.skip("this OS refuses an unprivileged symlink")


@pytest.fixture
def opened(monkeypatch) -> list[Path]:
    """Every path opened through builtins.open, io.open or os.open, resolved."""
    seen: list[Path] = []

    def spy(real):
        def wrapper(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                seen.append(Path(os.fsdecode(file)).resolve())
            return real(file, *args, **kwargs)
        return wrapper

    monkeypatch.setattr(builtins, "open", spy(builtins.open))
    monkeypatch.setattr(io, "open", spy(io.open))
    monkeypatch.setattr(os, "open", spy(os.open))
    return seen


def test_outside_skill_md_symlink_is_refused_unread(tmp_path, opened):
    outside = tmp_path / "secret.md"
    outside.write_text(_SKILL.format(name="secret"), encoding="utf-8")
    root = tmp_path / "root"
    (root / "skills" / "sf").mkdir(parents=True)
    _link(root / "skills" / "sf" / "SKILL.md", outside)
    (root / "skills.yaml").write_text("- name: sf\n", encoding="utf-8")
    opened.clear()  # the setup's own writes

    entries = read_skill_manifest(root)

    assert entries[0].skill is None
    assert "SKILL.md resolves outside root" in entries[0].error
    assert (root / "skills.yaml").resolve() in opened  # the spy sees the reads
    assert outside.resolve() not in opened


def test_inside_skill_md_symlink_still_loads(tmp_path, opened):
    root = tmp_path / "root"
    shared = root / "shared" / "SKILL.md"
    shared.parent.mkdir(parents=True)
    shared.write_text(_SKILL.format(name="inside"), encoding="utf-8")
    (root / "skills" / "sf").mkdir(parents=True)
    _link(root / "skills" / "sf" / "SKILL.md", shared)
    (root / "skills.yaml").write_text("- name: sf\n", encoding="utf-8")
    opened.clear()  # the setup's own writes

    entries = read_skill_manifest(root)

    assert entries[0].error is None
    assert entries[0].skill.name == "inside"
    assert entries[0].skill.description == "Use for inside things."
    assert shared.resolve() in opened


def test_looping_skill_md_is_an_error_row_and_the_next_entry_loads(tmp_path):
    root = tmp_path / "root"
    (root / "skills" / "loop").mkdir(parents=True)
    _link(root / "skills" / "loop" / "SKILL.md", Path("SKILL.md"))
    (root / "skills" / "ok").mkdir()
    (root / "skills" / "ok" / "SKILL.md").write_text(_SKILL.format(name="ok"), encoding="utf-8")
    (root / "skills.yaml").write_text("- name: loop\n- name: ok\n", encoding="utf-8")

    entries = read_skill_manifest(root)

    assert entries[0].skill is None
    assert entries[0].error
    assert entries[1].skill.name == "ok"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="needs a POSIX non-root user")
def test_unreadable_skills_dir_is_an_error_row_and_the_next_entry_loads(tmp_path):
    root = tmp_path / "root"
    (root / "skills" / "sf").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "other" / "SKILL.md").write_text(_SKILL.format(name="other"), encoding="utf-8")
    (root / "skills.yaml").write_text("- name: sf\n- name: o\n  catalog_path: other\n", encoding="utf-8")
    (root / "skills").chmod(0)
    try:
        entries = read_skill_manifest(root)
    finally:
        (root / "skills").chmod(0o755)

    assert entries[0].skill is None
    assert "Permission denied" in entries[0].error
    assert entries[1].skill.name == "other"
