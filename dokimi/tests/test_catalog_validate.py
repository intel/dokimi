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
from dokimi.skills import discover_skills
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
    # "Use for " keeps a when-clause in the description, so only the length is in question.
    _skill(tmp_path / "skills", "at-limit", name="at-limit", description="Use for " + "d" * 1016)
    _skill(tmp_path / "skills", "over-limit", name="over-limit", description="Use for " + "d" * 1017)

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
    # Not a duplicate, but a mirror that has drifted: both copies say so.
    assert [[f.code for f in r.findings] for r in reviews] == [["mirror-drift"], ["mirror-drift"]]


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


def _root_skill(root: Path, frontmatter: str) -> None:
    root.mkdir(exist_ok=True)
    (root / "SKILL.md").write_text(f"---\n{frontmatter}---\n# z\n", encoding="utf-8")


def test_root_skill_still_gets_the_name_format_check(tmp_path, capsys):
    root = tmp_path / "checkout"
    _root_skill(root, "name: Z_z\ndescription: Use for z.\n")
    report, _ = _scan_json(root, capsys)

    assert [f["code"] for f in report["skills"][0]["findings"]] == ["name-format"]


def test_root_skill_without_a_name_does_not_cite_the_checkout_directory(tmp_path, capsys):
    root = tmp_path / "checkout"
    _root_skill(root, "description: Use for z.\n")
    report, _ = _scan_json(root, capsys)

    [finding] = report["skills"][0]["findings"]
    assert finding["code"] == "name-missing"
    assert "directory name" not in finding["message"]
    assert "checkout" not in finding["message"]


def test_a_skill_without_a_manifest_status_has_a_null_status_in_json(tmp_path, capsys):
    _skill(tmp_path / "skills", "a", name="a")
    report, _ = _scan_json(tmp_path, capsys)

    assert report["skills"][0]["manifest_status"] is None


def test_a_task_without_a_manifest_status_has_a_null_status_in_json(tmp_path, capsys):
    _skill(tmp_path / "skills", "s", name="s")
    (tmp_path / "skills.yaml").write_text(
        'skills:\n  - name: s\n    catalog_path: skills/s\n', encoding="utf-8")
    (tmp_path / "evaluation" / "harbor").mkdir(parents=True)
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        json.dumps({"schema_version": "1.0", "suites": [{"skill": "s", "tasks": [
            {"name": "later", "track": "executable"}]}]}),
        encoding="utf-8",
    )
    report, _ = _scan_json(tmp_path, capsys, kind="tasks")

    assert [t["manifest_status"] for t in report["tasks"]] == [None]


def test_a_duplicate_found_through_a_symlinked_root_is_a_row_not_a_traceback(tmp_path, capsys):
    real = tmp_path / "real"
    for name in ("a", "b"):
        _skill(real / "skills", name, name="same")
    (real / "skills.yaml").write_text(
        f"skills:\n  - name: a\n    catalog_path: {real / 'skills' / 'a'}\n"
        "  - name: b\n    catalog_path: skills/b\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    report, _ = _scan_json(link, capsys)

    second = next(s for s in report["skills"] if s["duplicate_of"])
    assert second["duplicate_of"].endswith("skills/a")
    assert "duplicate" in {f["code"] for f in second["findings"]}


def test_a_root_skill_reached_by_an_equivalent_spelling_skips_the_directory_check(
        tmp_path, capsys):
    root = tmp_path / "checkout"
    _root_skill(root, "name: zzz\ndescription: Use for z.\n")
    (root / "sub").mkdir()
    (root / "skills.yaml").write_text(
        "skills:\n  - name: zzz\n    catalog_path: sub/..\n", encoding="utf-8")
    report, _ = _scan_json(root, capsys)

    assert report["skills"][0]["findings"] == []


def test_a_scan_without_a_manifest_leaves_out_a_template_as_discover_skills_does(
        tmp_path, capsys):
    _skill(tmp_path / "skills", "foo", name="foo")
    _skill(tmp_path / "templates", "bar", name="Bar Template")
    report, code = _scan_json(tmp_path, capsys, strict=True)

    assert [s["name"] for s in report["skills"]] == ["foo"]
    assert code == 0


def test_a_mirror_names_as_original_the_copy_discover_skills_keeps(tmp_path):
    _skill(tmp_path / "plugin" / "skills", "foo", name="foo")
    _skill(tmp_path / "skills", "foo", name="foo")
    entries = read_skill_manifest(tmp_path)
    duplicate_of = {e.path.relative_to(tmp_path).as_posix(): r.duplicate_of
                    for e, r in zip(entries, review_skills(entries), strict=True)}

    assert [s.path.parent for s in discover_skills(tmp_path)] == [tmp_path / "skills" / "foo"]
    assert duplicate_of == {"plugin/skills/foo": "skills/foo", "skills/foo": None}


@pytest.mark.parametrize("manifest", [True, False])
def test_broken_yaml_outside_the_root_is_not_quoted_in_the_report(tmp_path, capsys, manifest):
    outside = tmp_path / "private.md"
    outside.write_text("---\napi_token: sk-SECRET-12345: x\n---\nbody\n", encoding="utf-8")
    root = tmp_path / "cat"
    (root / "skills" / "a").mkdir(parents=True)
    (root / "skills" / "a" / "SKILL.md").symlink_to(outside)
    if manifest:
        (root / "skills.yaml").write_text("- name: a\n", encoding="utf-8")
    report, code = _scan_json(root, capsys)

    assert "SECRET" not in json.dumps(report)
    assert report["skills"][0]["state"] == "error" and code == 1


def test_default_kind_exits_1_on_a_broken_skill_and_no_tasks(tmp_path, capsys):
    (tmp_path / "skills" / "bad").mkdir(parents=True)
    (tmp_path / "skills" / "bad" / "SKILL.md").write_text("---\nname: bad\n---\n", encoding="utf-8")
    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)

    assert exc.value.code == 1
    assert "ERROR" in capsys.readouterr().out


@pytest.mark.parametrize("manifest", [True, False])
def test_an_unreadable_skill_is_an_error_row_and_the_rest_are_listed(
        tmp_path, capsys, monkeypatch, manifest):
    import dokimi.external.catalog as external_catalog

    real_load = external_catalog.load_skill

    def load(path):
        if "b" in Path(path).parts:
            raise PermissionError(13, "Permission denied", str(path))
        return real_load(path)

    monkeypatch.setattr(external_catalog, "load_skill", load)
    for name in ("a", "b"):
        _skill(tmp_path / "skills", name, name=name)
    if manifest:
        (tmp_path / "skills.yaml").write_text("- name: a\n- name: b\n", encoding="utf-8")
    report, code = _scan_json(tmp_path, capsys)

    states = {s["name"]: (s["state"], s["error"]) for s in report["skills"]}
    assert states["a"] == ("ok", None)
    assert states["b"][0] == "error" and "Permission denied" in states["b"][1]
    assert code == 1



def _codes(review) -> list[str]:
    return [f.code for f in review.findings]


def test_relative_link_to_a_file_that_is_not_there_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "guided", name="guided", description="Use for guided work.",
           body="See [the guide](references/guide.md).\n")
    bundled = _skill(
        tmp_path / "skills",
        "bundled",
        name="bundled",
        description="Use for bundled work.",
        body="Details in [the guide](references/guide.md).\n",
    )
    (bundled / "references").mkdir()
    (bundled / "references" / "guide.md").write_text("# G\n", encoding="utf-8")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["guided"].state == "warn"
    assert _codes(reviews["guided"]) == ["link-missing"]
    assert reviews["bundled"].state == "ok"


def test_link_that_exists_from_the_catalog_root_says_the_base_is_wrong(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    _skill(tmp_path / "skills", "runner", name="runner", body="Run [it](scripts/run.py).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    [finding] = review.findings
    assert finding.code == "link-missing"
    assert "the base is wrong" in finding.message


def test_links_in_code_fences_urls_and_anchors_are_not_checked(tmp_path):
    body = (
        "Read [the site](https://example.com/docs) or [below](#usage).\n"
        "```markdown\n"
        "See [an example](references/missing.md).\n"
        "```\n"
        "~~~\n"
        "[also](scripts/missing.sh)\n"
        "~~~\n"
    )
    _skill(tmp_path / "skills", "clean", name="clean", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_skill_dir_variable_resolves_inside_the_skill(tmp_path):
    skill = _skill(tmp_path / "skills", "vars", name="vars",
                   body="[a](${CLAUDE_SKILL_DIR}/references/a.md) [b](${CLAUDE_SKILL_DIR}/b.md)\n")
    (skill / "references").mkdir()
    (skill / "references" / "a.md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'${CLAUDE_SKILL_DIR}/b.md'" in review.findings[0].message


def test_link_out_of_the_skill_dir_to_an_existing_file_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", body="See [notes](../../shared/notes.md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_link_out_of_the_skill_dir_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "reach", name="reach", body="See [notes](../../nowhere.md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


def test_dotdot_code_span_to_an_existing_file_outside_the_skill_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(
        tmp_path / "skills",
        "reach",
        name="reach",
        body="Keep `../../shared/notes.md` next to it. Edit `docs/missing.md` in your project.\n",
    )

    [review] = review_skills(read_skill_manifest(tmp_path))
    # The code span naming a missing project path is not checked: only the escape is reported.
    assert review.state == "warn"
    assert _codes(review) == ["path-escapes-skill"]


def test_description_without_a_when_clause_is_a_warning(tmp_path):
    _skill(
        tmp_path / "skills", "plain", name="plain", description="Builds release reports from logs."
    )
    _skill(tmp_path / "skills", "said", name="said",
           description="Summarises deploy outcomes. Use when a release is cut.")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["plain"].state == "warn"
    assert _codes(reviews["plain"]) == ["description-no-when"]
    assert reviews["said"].state == "ok"


def test_hidden_skill_is_exempt_from_the_description_checks(tmp_path):
    _skill(tmp_path / "skills", "hidden", name="hidden", description="Builds release reports.",
           extra="disable-model-invocation: true\n")
    _skill(tmp_path / "skills", "twin", name="twin", description="Builds release reports.",
           extra="disable-model-invocation: true\n")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["hidden"].state == "ok"
    assert reviews["twin"].state == "ok"


def test_near_duplicate_descriptions_warn_on_both_skills(tmp_path):
    _skill(tmp_path / "skills", "style-check", name="style-check",
           description="Use when reviewing Python pull requests for style and bugs.")
    _skill(tmp_path / "skills", "style-lint", name="style-lint",
           description="Use when reviewing Python pull requests for style and typing.")

    reviews = _reviews_by_name(tmp_path)
    assert _codes(reviews["style-check"]) == ["description-near-duplicate"]
    assert _codes(reviews["style-lint"]) == ["description-near-duplicate"]
    assert "skills/style-lint" in reviews["style-check"].findings[0].message


def test_unrelated_descriptions_are_not_near_duplicates(tmp_path):
    _skill(tmp_path / "skills", "deploy", name="deploy",
           description="Use when deploying containers to Kubernetes clusters.")
    _skill(tmp_path / "skills", "notes", name="notes",
           description="Use when writing release notes for a changelog.")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert all(r.state == "ok" for r in reviews)


def test_identical_and_same_named_pairs_get_no_near_duplicate_warning(tmp_path):
    same = "Use when reviewing Python pull requests for style and bugs."
    _skill(tmp_path / "skills", "copy-a", name="copy", description=same)
    _skill(tmp_path / "skills", "copy-b", name="copy", description=same)
    other = "Builds Kubernetes container deployment manifests."
    _skill(tmp_path / "plugins", "x", name="x", description=other, body="# Old\n")
    _skill(tmp_path / "skills", "x", name="x", description=other, body="# New\n")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert len(reviews) == 4
    assert all("description-near-duplicate" not in _codes(r) for r in reviews)


def _words(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{c}" for c in "abcdefghijklmnopqrstuvwxyz"[:count])


@pytest.mark.parametrize(
    ("shared", "own_a", "own_b", "warns"),
    [(2, 1, 1, True), (24, 13, 12, False)],  # 2/4 = 0.5 exactly; 24/49 just below it
)
def test_near_duplicate_threshold_is_half_the_words(tmp_path, shared, own_a, own_b, warns):
    common = _words("zq", shared)
    _skill(tmp_path / "skills", "one", name="one",
           description=f"{common} {_words('xa', own_a)}")
    _skill(tmp_path / "skills", "two", name="two",
           description=f"{common} {_words('xb', own_b)}")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert ("description-near-duplicate" in _codes(reviews[0])) is warns
    assert ("description-near-duplicate" in _codes(reviews[1])) is warns


def test_drifted_copies_of_one_skill_warn_on_both_and_name_each_other(tmp_path):
    _skill(tmp_path / "plugins", "x", name="x", description="Use for x.", body="# Old\n")
    _skill(tmp_path / "skills", "x", name="x", description="Use for x.", body="# New\n")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert all(r.duplicate_of is None for r in reviews)
    assert [_codes(r) for r in reviews] == [["mirror-drift"], ["mirror-drift"]]
    assert "differs from plugins/x" in reviews[1].findings[0].message
    assert "differs from skills/x" in reviews[0].findings[0].message


def test_identical_copies_are_a_duplicate_and_not_drift(tmp_path):
    _skill(tmp_path / "plugins", "x", name="x", description="Use for x.")
    _skill(tmp_path / "skills", "x", name="x", description="Use for x.")

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert [_codes(r) for r in reviews if r.duplicate_of is not None] == [["duplicate"]]
    assert all("mirror-drift" not in _codes(r) for r in reviews)


def test_new_warnings_do_not_fail_the_scan_even_under_strict(tmp_path, capsys):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Builds reports.",
           body="See [notes](../../shared/notes.md).\n")
    report, code = _scan_json(tmp_path, capsys, strict=True)
    assert code == 0
    assert report["skills"][0]["state"] == "warn"
    assert {f["code"] for f in report["skills"][0]["findings"]} == {
        "description-no-when", "link-escapes-skill"}

    _skill(tmp_path / "skills", "example", name="example", description="Builds reports.",
           body="Format: [Title](URL)\n")
    report, code = _scan_json(tmp_path, capsys, strict=True)
    assert code == 0
    states = {row["name"]: row["state"] for row in report["skills"]}
    assert states == {"reach": "warn", "example": "warn"}


def test_the_scan_sees_a_mirror_that_discover_skills_leaves_out(tmp_path):
    # discover_skills counts a byte-identical mirror once; the catalog review must still see both.
    _skill(tmp_path / "plugins", "x", name="x", description="Use for x.")
    _skill(tmp_path / "skills", "x", name="x", description="Use for x.")
    assert [s.path.parent.relative_to(tmp_path).as_posix() for s in discover_skills(tmp_path)] == [
        "plugins/x"
    ]

    reviews = review_skills(read_skill_manifest(tmp_path))
    assert [r.duplicate_of for r in reviews] == [None, "plugins/x"]


def test_a_link_target_the_os_refuses_is_a_missing_link_not_a_crash(tmp_path):
    # A name over 255 bytes in one path part: Path.exists() raises on Python 3.11 to 3.13.
    too_long = "a" * 300
    _skill(tmp_path / "skills", "healthy", name="healthy", description="Use for healthy.",
           body="Run [it](references/a.md).\n")
    _skill(tmp_path / "skills", "longname", name="longname", description="Use for longname.",
           body=f"See [g]({too_long}.md).\n")

    reviews = _reviews_by_name(tmp_path)
    assert reviews["healthy"].state == "warn"
    assert _codes(reviews["longname"]) == ["link-missing"]


@pytest.mark.parametrize(
    "destination",
    ["tel:+15551234567", "urn:isbn:0451450523", "MAILTO:someone@example.com", "data:text/plain,x"],
)
def test_uri_scheme_destinations_are_not_files(tmp_path, destination):
    _skill(tmp_path / "skills", "uris", name="uris", description="Use for uris.",
           body=f"Reach us at [here]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_reference_definition_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "refs", name="refs", description="Use for refs.",
           body="See [the guide][g] for details.\n\n[g]: references/missing.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


def test_reference_definition_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [the notes][n].\n\n[n]: ../../shared/notes.md \"Notes\"\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_reference_definitions_that_name_no_file_are_not_checked(tmp_path):
    body = (
        "A footnote[^1] and [a site][web].\n\n"
        "[^1]: Footnote text, not a file.\n"
        "[web]: https://example.com/docs\n"
        "[anchor]: #usage\n"
        "```markdown\n"
        "[example]: references/missing.md\n"
        "```\n"
    )
    _skill(tmp_path / "skills", "clean", name="clean", description="Use for clean.", body=body)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


def test_angle_bracket_destination_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "angle", name="angle", description="Use for angle.",
           body="See [the guide](<references/missing.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


def test_angle_bracket_destination_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [notes](<../../shared/notes.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_angle_bracket_destination_with_an_anchor_out_of_the_skill_dir_is_a_warning(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach", name="reach", description="Use for reach.",
           body="See [setup](<../../shared/notes.md#setup>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-escapes-skill"]


def test_reference_definition_with_spaces_in_angle_brackets_to_a_missing_file_is_a_warning(tmp_path):
    _skill(tmp_path / "skills", "spaced", name="spaced", description="Use for spaced.",
           body="See [notes][n].\n\n[n]: <my notes.md>\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "line",
    ["[Note]: read this first", "[Step 1]: Run the tests", "[--verbose]: print more"],
)
def test_prose_line_that_starts_with_a_bracketed_label_is_not_a_definition(tmp_path, line):
    _skill(tmp_path / "skills", "prose", name="prose", description="Use for prose.",
           body=f"Start here.\n\n{line}\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "ok"
    assert review.findings == []


@pytest.mark.parametrize("destination", ["run.py:42", "run.py:10-20", "scripts/run.py:42:7"])
def test_a_file_line_link_to_a_missing_file_is_still_a_warning(tmp_path, destination):
    # "run.py:42" is read as a path, not a URI, so it is checked and the missing file is flagged.
    _skill(tmp_path / "skills", "fileline", name="fileline", description="Use for fileline.",
           body=f"See [the entry point]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.state == "warn"
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "destination", ["z39.50r:example.org/database", "x.y+z:records/item"],
)
def test_dotted_uri_scheme_destinations_are_not_files(tmp_path, destination):
    _skill(tmp_path / "skills", "dotted", name="dotted", description="Use for dotted.",
           body=f"Look up [the record]({destination}).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_skill_dir_variable_with_another_variable_is_not_read_as_a_file(tmp_path):
    _skill(tmp_path / "skills", "tpl", name="tpl", description="Use for tpl.",
           body="Run [it](${CLAUDE_SKILL_DIR}/scripts/${SCRIPT_NAME}.py).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_root_skill_and_a_nested_skill_named_like_the_checkout_are_not_mirror_drift(tmp_path):
    # The root SKILL.md is named by the checkout's directory, so it is no copy of skills/tools.
    root = tmp_path / "tools"
    _root_skill(root, "name: root-skill\ndescription: Use for root work.\n")
    _skill(root, "skills/tools", name="tools", description="Use for tool work.")

    reviews = _reviews_by_name(root)
    assert _codes(reviews["root-skill"]) == []
    assert _codes(reviews["tools"]) == []


def test_root_skills_of_two_checkouts_are_near_duplicates_not_mirror_drift(tmp_path):
    # Both root skills reached as "sub/.." have no directory name, so they never mirror each other.
    entries = []
    for checkout, ending in (("a/repo", "bugs"), ("b/repo", "typing")):
        root = tmp_path / checkout
        root.mkdir(parents=True)
        _root_skill(
            root, f"name: r{checkout[0]}\ndescription: Use when reviewing Python pull "
                  f"requests for style and {ending}.\n")
        (root / "sub").mkdir()
        (root / "skills.yaml").write_text(
            f"skills:\n  - name: r{checkout[0]}\n    catalog_path: sub/..\n", encoding="utf-8")
        entries.extend(read_skill_manifest(root))

    reviews = review_skills(entries)
    assert [_codes(r) for r in reviews] == [["description-near-duplicate"]] * 2


def test_near_duplicate_check_reads_non_ascii_description_words(tmp_path):
    shared = "Use when проверка исходного кода необходима."
    _skill(tmp_path / "skills", "code-check", name="code-check", description=shared)
    _skill(tmp_path / "skills", "code-audit", name="code-audit", description=shared)

    reviews = _reviews_by_name(tmp_path)
    assert _codes(reviews["code-check"]) == ["description-near-duplicate"]
    assert _codes(reviews["code-audit"]) == ["description-near-duplicate"]


def test_a_file_symbol_link_to_a_missing_file_is_still_a_warning(tmp_path):
    _skill(tmp_path / "skills", "symbol", name="symbol", description="Use for symbol.",
           body="See [the entry point](run.py:main).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


@pytest.mark.parametrize(
    "description",
    [
        "Run database migrations when deploying a new release.",
        "Diagnose failures when debugging production incidents.",
        "Format commit messages when the user asks for a commit.",
    ],
)
def test_a_general_when_clause_is_not_a_description_warning(tmp_path, description):
    _skill(tmp_path / "skills", "clause", name="clause", description=description)

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == []


@pytest.mark.parametrize("body", [
    "See [the entry point](run.py:42).\n",
    "See [the entry point](run.py:main).\n",
    "See [the entry point](run.py:10-20).\n",
    "See [the entry point](scripts/run.py:42:7).\n",
])
def test_an_existing_file_with_a_line_or_symbol_suffix_is_not_a_missing_link(tmp_path, body):
    skill_dir = _skill(tmp_path / "skills", "bundled-run", name="bundled-run",
                       description="Use for bundled run.", body=body)
    (skill_dir / "run.py").write_text("print('x')\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_an_escaping_link_with_a_line_suffix_is_named_as_escaping(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "run.py").write_text("print('x')\n", encoding="utf-8")
    _skill(tmp_path / "skills", "reach-run", name="reach-run", description="Use for reach run.",
           body="See [the entry point](../../shared/run.py:42).\n"
                "Also `../../shared/run.py:main`.\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert sorted(_codes(review)) == ["link-escapes-skill", "path-escapes-skill"]
