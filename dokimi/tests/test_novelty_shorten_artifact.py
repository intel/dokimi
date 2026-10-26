# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty shorten`: an artifact that cannot be loaded exits with a message, not a traceback."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.atoms import save_atoms
from dokimi.novelty.spans import segment


def _shorten(tmp_path, artifact):
    atoms = tmp_path / "atoms.json"
    save_atoms([], segment("# H\n\nSome text.\n"), atoms, extractor="e", cap=None)
    args = build_parser().parse_args([
        "novelty", "shorten", "--artifact", artifact, "--atoms", str(atoms), "--lexical-only",
    ])
    args.func(args)


def test_a_missing_text_artifact_exits_naming_it(tmp_path):
    missing = tmp_path / "missing.md"
    with pytest.raises(SystemExit) as exc:
        _shorten(tmp_path, f"text:{missing}")
    assert str(exc.value) == f"cannot load --artifact text:{missing}: artifact not found: {missing}"


def test_a_non_utf8_text_artifact_exits_naming_it(tmp_path):
    binary = tmp_path / "bin.md"
    binary.write_bytes(bytes([0xFF, 0xFE, 0x00]))
    with pytest.raises(SystemExit) as exc:
        _shorten(tmp_path, f"text:{binary}")
    assert str(exc.value).startswith(f"cannot load --artifact text:{binary}: ")


def test_a_skill_file_without_a_closing_frontmatter_fence_exits_naming_it(tmp_path, monkeypatch):
    from types import SimpleNamespace

    entry = tmp_path / "SKILL.md"
    entry.write_text("---\nname: s\ndescription: d\n\n# H\n", encoding="utf-8")
    monkeypatch.setattr("dokimi.skills.loader.load_skill", lambda _p: SimpleNamespace(
        path=entry, name="s", description="d", body="# H\n"))
    with pytest.raises(SystemExit) as exc:
        _shorten(tmp_path, f"skill:{tmp_path}")
    assert str(exc.value) == (f"cannot load --artifact skill:{tmp_path}: "
                              f"{entry} has no closing '---' after its frontmatter")
