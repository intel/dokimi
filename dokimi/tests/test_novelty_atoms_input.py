# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty probe` and `novelty shorten` on an --atoms file they cannot read."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser


_WRONG_SHAPES = {
    "wrong_shape": '{"atoms": [{"unexpected": 1}], "spans": []}',
    "atoms_not_a_list": '{"atoms": "x"}',
    "bundle_is_a_list": '{"atoms": [], "spans": [], "bundle": [1]}',
    "bundle_is_a_number": '{"atoms": [], "spans": [], "bundle": 5}',
    "bundle_files_not_objects":
        '{"atoms": [], "spans": [], "bundle": {"include_resources": true, "files": [1]}}',
    "bundle_files_not_a_list": '{"atoms": [], "spans": [], "bundle": {"files": "x"}}',
}


def _atoms_file(tmp_path, kind):
    path = tmp_path / "atoms.json"
    if kind == "invalid_json":
        path.write_text("{not json", encoding="utf-8")
    elif kind == "not_utf8":
        path.write_bytes(b"\xff\xfe\x00")
    elif kind == "directory":
        path.mkdir()
    elif kind in _WRONG_SHAPES:
        path.write_text(_WRONG_SHAPES[kind], encoding="utf-8")
    return path


@pytest.mark.parametrize("sub", ["probe", "shorten"])
@pytest.mark.parametrize("kind", ["missing", "invalid_json", "not_utf8", "directory", *_WRONG_SHAPES])
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


@pytest.mark.parametrize("bundle", ['null', '{}', '{"files": []}'])
def test_a_bundle_block_of_the_right_shape_is_not_refused(tmp_path, bundle):
    from dokimi.novelty.atoms import load_atoms_or_exit

    path = tmp_path / "atoms.json"
    path.write_text('{"atoms": [], "spans": [], "bundle": %s}' % bundle, encoding="utf-8")
    atoms, spans, record = load_atoms_or_exit(path)
    assert (atoms, spans, record["bundle"]) == ([], [], __import__("json").loads(bundle))
