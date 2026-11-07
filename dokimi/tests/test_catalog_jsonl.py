# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --per-skill --jsonl``: one row per skill as it is reviewed, with ``--jobs`` and ``--resume``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.utils import InputError


def _skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when {name} runs.\n---\n# {name}\n", encoding="utf-8"
    )


def _catalog(root: Path) -> None:
    """Three good skills, then delta, which has no description and does not load."""
    for name in ("alpha", "bravo", "charlie"):
        _skill(root, name)
    delta = root / "skills" / "delta"
    delta.mkdir(parents=True)
    (delta / "SKILL.md").write_text("---\nname: delta\n---\n# no description\n", encoding="utf-8")


def _args(argv: list[str]):
    return build_parser().parse_args(["catalog", "check", "--kind", "skills", *argv])


def _run(argv: list[str], capsys) -> tuple[str, int]:
    """stdout and the exit code of a scan; 0 when it returns normally, 2 when it refuses its input."""
    args = _args(argv)
    try:
        args.func(args)
    except InputError:
        return capsys.readouterr().out, 2
    except SystemExit as exc:
        return capsys.readouterr().out, int(exc.code or 0)
    return capsys.readouterr().out, 0


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writes_one_row_per_skill_in_catalog_order_and_prints_only_the_counts(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "out.jsonl"

    out, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)

    assert [row["relpath"] for row in _rows(out_path)] == [
        "skills/alpha", "skills/bravo", "skills/charlie", "skills/delta"
    ]
    assert "# skills (4 declared" in out
    assert "skills/alpha" not in out  # per-skill lines go to the file, not to stdout
    assert code == 1  # delta does not load


def test_more_jobs_write_the_same_rows_in_the_same_order(tmp_path, capsys):
    # More skills than one 32-skill chunk per worker, so the workers finish out of order.
    for i in range(100):
        _skill(tmp_path, f"s{i:03d}")
    one = tmp_path / "jobs1.jsonl"
    many = tmp_path / "jobs3.jsonl"

    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(one), "--jobs", "1"], capsys)
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(many), "--jobs", "3"], capsys)

    relpaths = [row["relpath"] for row in _rows(many)]
    assert relpaths == sorted(relpaths) and len(relpaths) == 100
    assert _rows(many) == _rows(one)


def test_a_jobs_worker_that_dies_ends_the_scan_instead_of_hanging():
    from dokimi.commands.catalog_scan import _review_rows

    # Each item makes its worker exit at once, as an out-of-memory kill would.
    with pytest.raises(SystemExit, match="worker process died"):
        list(_review_rows([3, 3, 3, 3], 2, review=os._exit))


def test_resume_reviews_only_the_skills_without_a_row(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "resume.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)
    rows = _rows(out_path)
    rows[0]["sentinel"] = "kept"  # a row that is reviewed again would lose this mark
    # A killed run: bravo has no row, and the last line is cut short.
    kept = [row for row in rows if row["relpath"] != "skills/bravo"]
    out_path.write_text("".join(json.dumps(row) + "\n" for row in kept) + '{"name": "cha', encoding="utf-8")

    out, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"], capsys)

    resumed = _rows(out_path)
    assert [row["relpath"] for row in resumed] == [
        "skills/alpha", "skills/charlie", "skills/delta", "skills/bravo"
    ]
    assert resumed[0]["sentinel"] == "kept"
    assert "3 resumed" in out
    assert code == 1


def test_resume_keeps_the_exit_code_of_a_load_error_it_skips(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "resume-fail.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)

    _, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"], capsys)

    assert code == 1
    assert len(_rows(out_path)) == 4


def test_without_resume_an_existing_jsonl_file_is_replaced(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "replace.jsonl"
    out_path.write_text('{"name": "stale"}\n', encoding="utf-8")

    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)

    assert [row["name"] for row in _rows(out_path)] == ["alpha", "bravo", "charlie", "delta"]


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--jsonl", "x.jsonl"], "need --per-skill"),
        (["--jobs", "2"], "need --per-skill"),
        (["--jobs", "1"], "need --per-skill"),
        (["--per-skill", "--resume"], "needs --jsonl"),
        (["--per-skill", "--jsonl", "x.jsonl", "--format", "json"], "drop --format json"),
    ],
)
def test_flag_combinations_the_scan_cannot_honour_are_refused(tmp_path, flags, message):
    _catalog(tmp_path)

    args = _args(["--root", str(tmp_path), *flags])
    with pytest.raises(InputError, match=message):
        args.func(args)


def test_an_unwritable_jsonl_path_is_refused_and_names_the_flag(tmp_path):
    _catalog(tmp_path)
    bad = tmp_path / "no-such-dir" / "out.jsonl"

    args = _args(["--root", str(tmp_path), "--per-skill", "--jsonl", str(bad)])
    with pytest.raises(InputError, match="cannot write --jsonl"):
        args.func(args)


def test_resume_with_skip_nested_leaves_out_a_nested_row_an_earlier_run_wrote(tmp_path, capsys):
    _skill(tmp_path, "alpha")
    inner = tmp_path / "skills" / "alpha" / "sub-beta"
    inner.mkdir(parents=True)
    (inner / "SKILL.md").write_text("---\nname: sub-beta\n---\n# no description\n", encoding="utf-8")
    out_path = tmp_path / "nested.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)
    assert len(_rows(out_path)) == 2  # the first run counted the broken nested skill

    out, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path),
                      "--resume", "--skip-nested"], capsys)

    assert code == 0
    assert "1 declared" in out
    assert "1 resumed" in out
    assert [row["name"] for row in _rows(out_path)] == ["alpha"]  # the nested row is pruned from the file


def _spec_error_skill(root: Path) -> None:
    """A skill that loads but has a spec error: its name is not its directory name."""
    gamma = root / "skills" / "gamma"
    gamma.mkdir(parents=True)
    (gamma / "SKILL.md").write_text(
        "---\nname: other\ndescription: Use when gamma runs.\n---\n# gamma\n", encoding="utf-8"
    )


def test_strict_on_the_per_skill_path_fails_on_a_spec_error_only(tmp_path, capsys):
    _skill(tmp_path, "alpha")
    _spec_error_skill(tmp_path)
    out_path = tmp_path / "strict.jsonl"

    _, lenient = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)
    _, strict = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--strict"], capsys)

    assert (lenient, strict) == (0, 1)


@pytest.mark.parametrize(
    "flags",
    [
        ["--per-skill"],
        ["--jobs", "2", "--per-skill"],
        ["--per-skill", "--jsonl", "x.jsonl"],
        ["--per-skill", "--jsonl", "x.jsonl", "--resume"],
        ["--skip-nested"],
    ],
)
def test_kind_tasks_refuses_the_flags_that_apply_to_skills(tmp_path, flags):
    args = build_parser().parse_args(["catalog", "check", "--kind", "tasks", "--root", str(tmp_path), *flags])

    with pytest.raises(InputError, match="--kind skills"):
        args.func(args)


def test_a_complete_line_that_is_not_a_scan_row_is_refused_and_not_written_over(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "bad-row.jsonl"
    out_path.write_text('{"name": "alpha"}\n', encoding="utf-8")

    args = _args(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"])
    with pytest.raises(InputError, match="line 1 is not a scan row"):
        args.func(args)

    assert out_path.read_text(encoding="utf-8") == '{"name": "alpha"}\n'


def test_resume_repairs_a_last_line_with_no_newline_before_appending(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "no-newline.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)
    first = _rows(out_path)[0]
    # A killed run wrote the first row but not its newline.
    out_path.write_text(json.dumps(first), encoding="utf-8")

    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"], capsys)

    assert [row["relpath"] for row in _rows(out_path)] == [
        "skills/alpha", "skills/bravo", "skills/charlie", "skills/delta"
    ]


def test_a_run_killed_inside_a_multibyte_character_resumes(tmp_path, capsys):
    _catalog(tmp_path)
    out_path = tmp_path / "multibyte.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)
    first = _rows(out_path)[0]
    # The first row, then the first byte of a two-byte character cut short by the kill.
    out_path.write_bytes(json.dumps(first).encode("utf-8") + b"\n" + b"\xc3")

    _, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"], capsys)

    assert code == 1  # delta does not load
    assert len(_rows(out_path)) == 4


def test_resume_refuses_rows_that_have_no_unique_key(tmp_path, capsys):
    _skill(tmp_path, "alpha")
    (tmp_path / "skills.yaml").write_text("- name: alpha\n- {}\n- {foo: 1}\n", encoding="utf-8")
    out_path = tmp_path / "keys.jsonl"
    _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path)], capsys)

    _, code = _run(["--root", str(tmp_path), "--per-skill", "--jsonl", str(out_path), "--resume"], capsys)

    assert code == 2  # the two entries without a path both have the key name:?
