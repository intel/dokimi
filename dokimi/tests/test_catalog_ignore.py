# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --ignore CODE``: a finding code left out of the state, the text, and the --strict exit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external.validate import FINDING_CODES
from dokimi.utils import InputError


def _skill(root: Path, rel: str, name: str, description: str = "Use for things.") -> None:
    """Write ``<root>/<rel>/SKILL.md``; ``rel`` is the directory, ``name`` the frontmatter name."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n", encoding="utf-8"
    )


def _run(argv: list[str], capsys) -> tuple[str, int]:
    """stdout and the exit code of ``catalog check``; 2 when it refuses its flags."""
    args = build_parser().parse_args(["catalog", "check", *argv])
    try:
        args.func(args)
    except InputError:
        return capsys.readouterr().out, 2
    except SystemExit as exc:
        return capsys.readouterr().out, int(exc.code or 0)
    return capsys.readouterr().out, 0


def _json_row(root: Path, capsys, *extra: str) -> tuple[dict, int]:
    out, code = _run(
        ["--root", str(root), "--kind", "skills", "--format", "json", "--strict", *extra], capsys
    )
    [row] = json.loads(out)["skills"]
    return row, code


def test_ignored_code_no_longer_sets_the_state_or_the_strict_exit(tmp_path, capsys):
    # The directory is named after a checkout and the frontmatter name is not: name-mismatch only.
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")

    row, code = _json_row(tmp_path, capsys)
    assert (row["state"], code) == ("error", 1)
    assert [f["code"] for f in row["findings"]] == ["name-mismatch"]

    row, code = _json_row(tmp_path, capsys, "--ignore", "name-mismatch")
    assert (row["state"], code) == ("ok", 0)
    assert row["findings"] == [] and row["ignored"] == ["name-mismatch"]


def test_ignore_drops_only_its_code_and_the_rest_still_sets_the_state(tmp_path, capsys):
    _skill(
        tmp_path / "skills",
        "writing-rules",
        name="writing-hookify-rules",
        description="Writes docs.",
    )

    row, code = _json_row(tmp_path, capsys, "--ignore", "name-mismatch")

    assert row["state"] == "warn" and code == 0  # --strict fails only on an error
    assert [f["code"] for f in row["findings"]] == ["description-no-when"]


def test_ignore_is_repeatable(tmp_path, capsys):
    _skill(
        tmp_path / "skills",
        "writing-rules",
        name="Writing Hookify Rules",
        description="Writes docs.",
    )

    row, code = _json_row(
        tmp_path,
        capsys,
        "--ignore",
        "name-mismatch",
        "--ignore",
        "name-format",
        "--ignore",
        "description-no-when",
    )

    assert (row["state"], row["findings"], code) == ("ok", [], 0)


def test_text_output_leaves_the_ignored_finding_out(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")

    out, code = _run(
        ["--root", str(tmp_path), "--kind", "skills", "--ignore", "name-mismatch"], capsys
    )

    assert "name-mismatch" not in out and "0 error" in out and code == 0


def test_unknown_code_is_a_usage_error_that_names_the_codes(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-rules")
    args = build_parser().parse_args(
        [
            "catalog",
            "check",
            "--root",
            str(tmp_path),
            "--kind",
            "skills",
            "--ignore",
            "name-mismtch",
        ]
    )

    with pytest.raises(InputError) as exc:
        args.func(args)

    message = str(exc.value)
    assert "name-mismtch" in message
    assert all(code in message for code in FINDING_CODES)
    assert capsys.readouterr().out == ""


def test_ignore_is_refused_for_tasks(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-rules")

    _, code = _run(
        ["--root", str(tmp_path), "--kind", "tasks", "--ignore", "name-mismatch"], capsys
    )

    assert code == 2


def test_resume_reviews_again_rows_written_under_other_ignore_codes(tmp_path, capsys):
    _skill(tmp_path / "skills", "writing-rules", name="writing-hookify-rules")
    _skill(tmp_path / "skills", "good", name="good")
    jsonl = tmp_path / "s.jsonl"
    base = ["--root", str(tmp_path), "--kind", "skills", "--per-skill", "--jsonl", str(jsonl)]
    assert _run(base, capsys)[1] == 0  # a first scan without --ignore, one row per skill

    out, code = _run([*base, "--resume", "--ignore", "name-mismatch"], capsys)

    rows = {
        json.loads(line)["relpath"]: json.loads(line) for line in jsonl.read_text().splitlines()
    }
    assert rows["skills/writing-rules"]["state"] == "ok"
    assert rows["skills/good"]["ignored"] == [
        "name-mismatch"
    ]  # no row was kept: every code set differs
    assert "resumed" not in out
    assert code == 0
