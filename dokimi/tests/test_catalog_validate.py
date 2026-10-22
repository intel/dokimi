# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spec and mirror review of ``catalog scan`` (``dokimi.external.validate``), and its ``--format json`` output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.external import read_skill_manifest, review_skills
from dokimi.utils import parse_frontmatter, parse_frontmatter_strict


def _skill(root: Path, rel: str, name: str | None = None, description: str = "Use for things.",
           extra: str = "", body: str = "# Body\n") -> Path:
    """Write ``<root>/<rel>/SKILL.md``; ``name`` None leaves the key out of the frontmatter."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    head = f"name: {name}\n" if name is not None else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\n{head}description: {description}\n{extra}---\n{body}", encoding="utf-8")
    return skill_dir


def _scan_json(
    root: Path, capsys, *, kind: str = "skills", strict: bool = False
) -> tuple[dict, int]:
    """Run ``catalog scan --format json``; return the parsed report and the exit code (0 when none raised)."""
    argv = ["catalog", "scan", "--root", str(root), "--kind", kind, "--format", "json"]
    args = build_parser().parse_args(argv + (["--strict"] if strict else []))
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return json.loads(capsys.readouterr().out), code


def _reviews_by_name(root: Path) -> dict:
    entries = read_skill_manifest(root)
    return {e.name: r for e, r in zip(entries, review_skills(entries), strict=True)}


def test_name_that_is_not_the_directory_name_is_an_error(tmp_path):
    _skill(tmp_path / "skills", "writing-rules", name="Writing Hookify Rules")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "error"
    codes = {f.code for f in review.findings}
    assert {"name-mismatch", "name-format"} <= codes


def test_title_case_name_matching_directory_is_still_not_kebab_case(tmp_path):
    _skill(tmp_path / "skills", "Writing", name="Writing")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "error"
    assert [f.code for f in review.findings] == ["name-format"]


def test_kebab_case_name_matching_directory_is_ok(tmp_path):
    _skill(tmp_path / "skills", "linux-perf-2", name="linux-perf-2")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_name_missing_from_frontmatter_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "no-name", name=None)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert [f.code for f in review.findings] == ["name-missing"]


def test_description_length_limit_is_1024_characters(tmp_path):
    _skill(tmp_path / "skills", "at-limit", name="at-limit", description="d" * 1024)
    _skill(tmp_path / "skills", "over-limit", name="over-limit", description="d" * 1025)

    reviews = _reviews_by_name(tmp_path)
    assert reviews["at-limit"].state == "ok"
    over = reviews["over-limit"]
    assert over.state == "error"
    assert [f.code for f in over.findings] == ["description-too-long"]


def test_identical_skill_md_is_a_warning_naming_the_first_copy(tmp_path):
    # A plugin copy with an extra bundled folder is still the same SKILL.md: the model sees the same text.
    _skill(tmp_path / "plugins", "x", name="x", description="Use for x.")
    copy = _skill(tmp_path / "skills", "x", name="x", description="Use for x.")
    (copy / "agents").mkdir()
    (copy / "agents" / "openai.yaml").write_text("a: 1\n", encoding="utf-8")

    reviews = review_skills(read_skill_manifest(tmp_path))
    originals = [r for r in reviews if r.duplicate_of is None]
    copies = [r for r in reviews if r.duplicate_of is not None]
    assert len(originals) == 1 and len(copies) == 1
    assert copies[0].duplicate_of == "plugins/x"
    assert copies[0].state == "warn"
    assert copies[0].findings[-1].code == "duplicate"
    assert copies[0].content_sha256 == originals[0].content_sha256


def test_different_skill_md_with_the_same_name_is_not_a_duplicate(tmp_path):
    _skill(tmp_path / "plugins", "x", name="x", description="Use for x.")
    _skill(tmp_path / "skills", "x", name="x", description="Use for x, a different text.")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert all(r.duplicate_of is None for r in reviews)
    assert all(r.state == "ok" for r in reviews)


def test_disable_model_invocation_is_reported_as_a_flag_not_a_finding(tmp_path):
    _skill(tmp_path / "skills", "hidden", name="hidden",
           extra="disable-model-invocation: true\n")
    _skill(tmp_path / "skills", "shown", name="shown")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["hidden"].disable_model_invocation is True
    assert reviews["hidden"].state == "ok"
    assert reviews["shown"].disable_model_invocation is False


def test_json_report_carries_the_findings_and_exits_0_on_a_spec_error_without_strict(
    tmp_path, capsys
):
    _skill(tmp_path / "skills", "claude-api", name="Claude API")
    report, code = _scan_json(tmp_path, capsys)

    assert code == 0
    [row] = report["skills"]
    assert row["state"] == "error"
    assert row["error"] is None
    assert {f["code"] for f in row["findings"]} >= {"name-mismatch", "name-format"}
    assert row["content_sha256"] and len(row["content_sha256"]) == 64


def test_strict_makes_a_spec_error_exit_1(tmp_path, capsys):
    _skill(tmp_path / "skills", "claude-api", name="Claude API")
    report, code = _scan_json(tmp_path, capsys, strict=True)

    assert code == 1
    assert report["skills"][0]["state"] == "error"


def test_strict_does_not_fail_on_warnings(tmp_path, capsys):
    _skill(tmp_path / "plugins", "x", name="x")
    _skill(tmp_path / "skills", "x", name="x")
    _skill(tmp_path / "skills", "no-name", name=None)
    _, code = _scan_json(tmp_path, capsys, strict=True)

    assert code == 0


def test_json_report_on_warnings_only_exits_0(tmp_path, capsys):
    _skill(tmp_path / "plugins", "x", name="x")
    _skill(tmp_path / "skills", "x", name="x")
    _skill(tmp_path / "skills", "hidden", name="hidden", extra="disable-model-invocation: yes\n")
    report, code = _scan_json(tmp_path, capsys)

    assert code == 0
    states = {row["path"].split(str(tmp_path) + "/")[1]: row["state"] for row in report["skills"]}
    assert states == {"plugins/x": "ok", "skills/x": "warn", "skills/hidden": "ok"}
    hidden = next(r for r in report["skills"] if r["name"] == "hidden")
    assert hidden["disable_model_invocation"] is True
    assert report["tasks"] == []


def test_json_report_keeps_load_errors_as_error_rows(tmp_path, capsys):
    (tmp_path / "skills.yaml").write_text(
        'skills:\n  - name: ghost\n    catalog_path: skills/does-not-exist\n', encoding="utf-8")
    report, code = _scan_json(tmp_path, capsys)

    assert code == 1
    [row] = report["skills"]
    assert row["state"] == "error"
    assert "does-not-exist" in row["error"]


def test_text_output_lists_each_finding_under_its_skill(tmp_path, capsys):
    _skill(tmp_path / "skills", "claude-api", name="Claude API")
    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"])
    args.func(args)  # no SystemExit: a spec error is printed, and only --strict makes it fail
    out = capsys.readouterr().out
    assert "# skills (1 declared" in out and "1 error" in out
    assert "ERROR: name 'Claude API' is not the directory name" in out
    assert "  error name-mismatch:" in out
    assert "  error name-format:" in out


BARE_COLON = "Use when: the user asks for a Claude API call"


def test_frontmatter_with_a_bare_colon_is_a_frontmatter_parse_error(tmp_path):
    # ``description: Use when: ...`` is not valid YAML; the lenient parser reads it as no metadata.
    _skill(tmp_path / "skills", "claude-api", name="claude-api", description=BARE_COLON)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "error"
    [finding] = review.findings
    assert finding.code == "frontmatter-parse"
    assert finding.level == "error"
    assert finding.message.startswith(
        "frontmatter is not valid YAML: mapping values are not allowed"
    )
    assert "\n" not in finding.message


def test_broken_yaml_is_reported_in_place_of_the_missing_description_error(tmp_path, capsys):
    _skill(tmp_path / "skills", "claude-api", name="claude-api", description=BARE_COLON)
    report, code = _scan_json(tmp_path, capsys)

    assert code == 1  # a skill that does not load exits 1 with or without --strict
    [row] = report["skills"]
    assert row["state"] == "error"
    assert "missing a 'description'" in row["error"]
    assert [f["code"] for f in row["findings"]] == ["frontmatter-parse"]
    assert row["content_sha256"] is None and row["duplicate_of"] is None


def test_strict_parse_raises_and_the_lenient_parse_still_returns_no_metadata():
    text = f"---\nname: claude-api\ndescription: {BARE_COLON}\n---\nbody\n"

    assert parse_frontmatter(text) == ({}, "body")
    with pytest.raises(yaml.YAMLError):
        parse_frontmatter_strict(text)


# --- review follow-up: edges of the name rules, duplicates, state, JSON rows -------------------------


@pytest.mark.parametrize("name", ["a" * 64, "a1-2b"])
def test_valid_names_matching_the_directory_have_no_finding(tmp_path, name):
    _skill(tmp_path / "skills", name, name=name)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


@pytest.mark.parametrize("name", ["a" * 65, "a--b", "a-", "-a", "a_b", "a b"])
def test_malformed_names_matching_the_directory_only_fail_name_format(tmp_path, name):
    _skill(tmp_path / "skills", name, name=f"'{name}'")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert [f.code for f in review.findings] == ["name-format"]


def test_same_frontmatter_with_a_different_body_is_not_a_duplicate(tmp_path):
    _skill(tmp_path / "plugins", "x", name="x", body="# One\n")
    _skill(tmp_path / "skills", "x", name="x", body="# Two\n")

    assert all(r.duplicate_of is None for r in review_skills(read_skill_manifest(tmp_path)))


def test_a_trailing_newline_difference_is_not_a_duplicate(tmp_path):
    _skill(tmp_path / "plugins", "x", name="x", body="# Body\n")
    _skill(tmp_path / "skills", "x", name="x", body="# Body\n\n")

    assert all(r.duplicate_of is None for r in review_skills(read_skill_manifest(tmp_path)))


def test_three_identical_copies_all_name_the_first(tmp_path):
    for parent in ("a-plugins", "b-plugins", "c-plugins"):
        _skill(tmp_path / parent, "x", name="x")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert reviews[0].duplicate_of is None
    assert [r.duplicate_of for r in reviews[1:]] == ["a-plugins/x", "a-plugins/x"]


def test_an_error_outranks_a_warning_in_the_state_and_fails_strict(tmp_path, capsys):
    _skill(tmp_path / "plugins", "Bad", name="Bad")
    _skill(tmp_path / "skills", "Bad", name="Bad")

    reviews = review_skills(read_skill_manifest(tmp_path))
    mirror = reviews[1]
    assert {f.level for f in mirror.findings} == {"warn", "error"}
    assert mirror.state == "error"
    _, code = _scan_json(tmp_path, capsys, strict=True)
    assert code == 1


def test_disable_model_invocation_false_does_not_hide_the_skill(tmp_path):
    _skill(tmp_path / "skills", "shown", name="shown", extra="disable-model-invocation: false\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.disable_model_invocation is False


def test_json_report_with_an_unquoted_date_status_does_not_crash(tmp_path, capsys):
    _skill(tmp_path / "skills", "a", name="a")
    (tmp_path / "skills.yaml").write_text(
        "skills:\n  - name: a\n    status: 2026-01-01\n    catalog_path: skills/a\n",
        encoding="utf-8",
    )
    report, code = _scan_json(tmp_path, capsys)

    assert code == 0
    assert report["skills"][0]["manifest_status"] == "2026-01-01"


def test_skill_at_the_scan_root_is_not_compared_with_the_root_directory_name(tmp_path, capsys):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: z\ndescription: Use for z.\n---\n# z\n", encoding="utf-8"
    )
    report, code = _scan_json(root, capsys, strict=True)

    assert code == 0
    assert report["skills"][0]["state"] == "ok"
    assert report["skills"][0]["findings"] == []


def test_json_tasks_array_carries_loaded_and_skipped_rows(tmp_path, capsys):
    _skill(tmp_path / "skills", "s", name="s")
    task = tmp_path / "evaluation" / "harbor" / "tasks" / "real"
    for sub in ("environment", "tests", "solution"):
        (task / sub).mkdir(parents=True)
    (task / "instruction.md").write_text("Do it.", encoding="utf-8")
    (task / "task.toml").write_text(
        '[metadata]\ndifficulty = "easy"\n\n[agent]\ntimeout_sec = 60\n\n'
        "[verifier]\ntimeout_sec = 10\n\n[environment]\n",
        encoding="utf-8",
    )
    (tmp_path / "skills.yaml").write_text(
        'skills:\n  - name: s\n    catalog_path: skills/s\n', encoding="utf-8")
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        json.dumps({"schema_version": "1.0", "suites": [{"skill": "s", "tasks": [
            {"name": "real", "status": "implemented", "track": "executable"},
            {"name": "later", "status": "planned", "track": "executable"}]}]}),
        encoding="utf-8",
    )
    report, code = _scan_json(tmp_path, capsys, kind="all")

    assert code == 0
    rows = {r["name"]: r for r in report["tasks"]}
    assert rows["real"]["state"] == "ok" and rows["real"]["track"] == "executable"
    assert rows["real"]["manifest_status"] == "implemented"
    assert rows["later"]["state"] == "skipped" and rows["later"]["reason"]
