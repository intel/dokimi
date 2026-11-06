# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``SKILL.md`` nested in another skill's directory is marked ``nested_in`` and can be skipped."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import nested_in_bundles, read_skill_manifest


def _write(skill_dir: Path, text: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")


def _outer_with_inner(root: Path, inner_text: str) -> None:
    _write(root / "skills" / "alpha", "---\nname: alpha\ndescription: Use when alpha work runs.\n---\n# Alpha\n")
    _write(root / "skills" / "alpha" / "sub-beta", inner_text)


_GOOD_INNER = "---\nname: sub-beta\ndescription: Use when beta work runs.\n---\n# Beta\n"
_BROKEN_INNER = "---\nname: sub-beta\n---\n# Beta without a description\n"


def _scan(argv: list[str], capsys) -> tuple[str, int]:
    args = build_parser().parse_args(["catalog", "scan", "--kind", "skills", *argv])
    try:
        args.func(args)
    except SystemExit as exc:
        return capsys.readouterr().out, int(exc.code or 0)
    return capsys.readouterr().out, 0


def test_the_inner_skill_is_marked_with_the_outer_skill_that_holds_it(tmp_path):
    _outer_with_inner(tmp_path, _GOOD_INNER)

    marks = dict(zip(
        [e.name for e in read_skill_manifest(tmp_path)],
        nested_in_bundles(read_skill_manifest(tmp_path), tmp_path),
        strict=True,
    ))

    assert marks == {"alpha": None, "sub-beta": "skills/alpha"}


def test_siblings_and_a_checkout_root_skill_do_not_enclose_each_other(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: root\ndescription: Use when the root runs.\n---\n", encoding="utf-8")
    _write(tmp_path / "skills" / "one", "---\nname: one\ndescription: Use when one runs.\n---\n")
    _write(tmp_path / "skills" / "two", "---\nname: two\ndescription: Use when two runs.\n---\n")

    marks = nested_in_bundles(read_skill_manifest(tmp_path), tmp_path)

    assert marks == [None, None, None]


def test_by_default_the_nested_skill_is_counted_and_marked_in_json(tmp_path, capsys):
    _outer_with_inner(tmp_path, _GOOD_INNER)

    out, code = _scan(["--root", str(tmp_path), "--format", "json"], capsys)

    rows = {row["name"]: row for row in json.loads(out)["skills"]}
    assert set(rows) == {"alpha", "sub-beta"}
    assert rows["sub-beta"]["nested_in"] == "skills/alpha"
    assert rows["alpha"]["nested_in"] is None
    assert code == 0


def test_skip_nested_leaves_the_inner_skill_out_and_so_its_load_error_too(tmp_path, capsys):
    _outer_with_inner(tmp_path, _BROKEN_INNER)

    _, code_counted = _scan(["--root", str(tmp_path)], capsys)
    out, code_skipped = _scan(["--root", str(tmp_path), "--skip-nested"], capsys)

    assert code_counted == 1
    assert code_skipped == 0
    assert "sub-beta" not in out
    assert "alpha" in out


def test_the_text_output_names_the_enclosing_skill(tmp_path, capsys):
    _outer_with_inner(tmp_path, _GOOD_INNER)

    out, _ = _scan(["--root", str(tmp_path)], capsys)

    assert "note nested in skills/alpha" in out


@pytest.mark.parametrize("flags", [[], ["--per-skill"]])
def test_nested_marks_are_the_same_in_both_modes(tmp_path, capsys, flags):
    _outer_with_inner(tmp_path, _GOOD_INNER)

    out, _ = _scan(["--root", str(tmp_path), "--format", "json", *flags], capsys)

    rows = {row["name"]: row["nested_in"] for row in json.loads(out)["skills"]}
    assert rows == {"alpha": None, "sub-beta": "skills/alpha"}
