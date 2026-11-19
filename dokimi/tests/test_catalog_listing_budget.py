# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#970: `catalog check --listing-budget` sums the skill listing, counts identical copies once, names what covers the excess."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.utils import InputError


def _skill(root, relpath, name, length, extra="", body="Body."):
    prefix = f"Use when reviewing {name} records. "
    description = prefix + "é" * (length - len(prefix))
    folder = root / relpath
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n", encoding="utf-8",
    )


def _scan(root, capsys, *flags):
    args = build_parser().parse_args(["catalog", "check", "--root", str(root), "--kind", "skills", *flags])
    args.func(args)
    return capsys.readouterr().out


def _catalog(tmp_path):
    for name, length in {"zebra": 95, "delta": 110, "alpha": 95, "small": 70}.items():
        _skill(tmp_path, name, name, length)
    _skill(tmp_path, "hidden", "hidden", 190, "disable-model-invocation: true\n")
    return 5 + 110 + 5 + 95 + 5 + 95 + 5 + 70  # name plus description, hidden left out


def test_over_budget_names_lines_until_the_excess_is_covered(tmp_path, capsys):
    """#970: over the cap the warning names the longest lines until their sum covers the excess."""
    total = _catalog(tmp_path)
    out = _scan(tmp_path, capsys, "--listing-budget", str(total - 1))
    assert f"# listing budget: {total}/{total - 1} characters across 4 distinct skills" in out
    assert "  warn listing-over-budget: over budget by 1; longest listing lines: delta (115 chars)" in out
    out = _scan(tmp_path, capsys, "--listing-budget", str(total - 215))
    assert "over budget by 215; longest listing lines: delta (115 chars), alpha (100 chars)" in out
    out = _scan(tmp_path, capsys, "--listing-budget", str(total - 216))
    assert "over budget by 216; longest listing lines: delta (115 chars), alpha (100 chars), zebra (100 chars)" in out


def test_json_carries_the_report_and_the_boundary_is_not_over(tmp_path, capsys):
    """#970: `--format json` carries `listing_budget`; a total equal to the cap is not over budget."""
    total = _catalog(tmp_path)
    report = json.loads(_scan(tmp_path, capsys, "--listing-budget", str(total - 1), "--format", "json"))
    assert report["listing_budget"] == {
        "chars": total, "skills": 4, "cap": total - 1, "excess": 1, "largest": [{"name": "delta", "chars": 115}],
    }
    at_cap = json.loads(_scan(tmp_path, capsys, "--listing-budget", str(total), "--format", "json"))
    assert at_cap["listing_budget"]["excess"] == 0 and at_cap["listing_budget"]["largest"] == []
    assert "warn listing-over-budget" not in _scan(tmp_path, capsys, "--listing-budget", str(total))


def test_identical_copies_count_once_and_the_description_is_capped(tmp_path, capsys):
    _skill(tmp_path, "a/scale", "scale", 1600)
    (tmp_path / "b" / "scale").mkdir(parents=True)
    (tmp_path / "b" / "scale" / "SKILL.md").write_bytes((tmp_path / "a" / "scale" / "SKILL.md").read_bytes())
    report = json.loads(_scan(tmp_path, capsys, "--listing-budget", "10", "--format", "json"))
    assert report["listing_budget"]["skills"] == 1
    assert report["listing_budget"]["chars"] == len("scale") + 1536


def test_without_the_flag_nothing_is_added(tmp_path, capsys):
    """#970: the report is opt in; without `--listing-budget` the output is unchanged."""
    _catalog(tmp_path)
    assert "listing_budget" not in json.loads(_scan(tmp_path, capsys, "--format", "json"))
    assert "# listing budget" not in _scan(tmp_path, capsys)


@pytest.mark.parametrize("flags", [["--per-skill"], ["--kind", "tasks"]])
def test_scope_is_refused(tmp_path, capsys, flags):
    """#970: `--listing-budget` needs a whole-catalog skill scan (docs/external-content.md), so `--per-skill` and `--kind tasks` exit 2."""
    _catalog(tmp_path)
    with pytest.raises(InputError, match="--listing-budget"):
        _scan(tmp_path, capsys, "--listing-budget", "100", *flags)


def test_when_to_use_is_part_of_the_listing_entry(tmp_path, capsys):
    """#970: the listing entry is the description, ' - ' and `when_to_use` (harnesses.skill_treatment.skill_listing), cut at the cap."""
    folder = tmp_path / "foo"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        f"---\nname: foo\ndescription: Use when testing.\nwhen_to_use: {'x' * 1000}\n---\nBody.\n", encoding="utf-8",
    )
    report = json.loads(_scan(tmp_path, capsys, "--listing-budget", "100", "--format", "json"))
    assert report["listing_budget"]["chars"] == 1023
    assert report["listing_budget"]["excess"] == 923
    _skill(tmp_path, "big/big", "big", 1600, "when_to_use: more\n")
    assert json.loads(_scan(tmp_path, capsys, "--listing-budget", "100", "--format", "json"))["listing_budget"]["chars"] == 1023 + 3 + 1536


def test_identical_files_that_load_under_different_names_are_two_entries(tmp_path, capsys):
    """#970: a copy counts once only when it lists the same entry; without `name` the directory names two entries."""
    for folder in ("alpha", "beta"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "SKILL.md").write_text("---\ndescription: Use when testing.\n---\nBody.\n", encoding="utf-8")
    report = json.loads(_scan(tmp_path, capsys, "--listing-budget", "30", "--format", "json"))["listing_budget"]
    assert (report["chars"], report["skills"], report["excess"]) == (43, 2, 13)
