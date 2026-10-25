# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty probe` and `novelty shorten` on an --atoms file they cannot read."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser


def _atoms_file(tmp_path, kind):
    path = tmp_path / "atoms.json"
    if kind == "invalid_json":
        path.write_text("{not json", encoding="utf-8")
    elif kind == "not_utf8":
        path.write_bytes(b"\xff\xfe\x00")
    elif kind == "directory":
        path.mkdir()
    elif kind == "wrong_shape":
        path.write_text('{"atoms": [{"unexpected": 1}], "spans": []}', encoding="utf-8")
    return path


@pytest.mark.parametrize("sub", ["probe", "shorten"])
@pytest.mark.parametrize("kind", ["missing", "invalid_json", "not_utf8", "directory", "wrong_shape"])
def test_an_unreadable_atoms_file_exits_with_a_message_naming_it(tmp_path, sub, kind):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nText.\n", encoding="utf-8")
    atoms = _atoms_file(tmp_path, kind)
    args = build_parser().parse_args(
        ["novelty", sub, "--artifact", f"skill:{skill}", "--atoms", str(atoms)]
    )
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    message = str(excinfo.value)
    assert message.startswith("Error: cannot read atoms from ")
    assert str(atoms) in message
