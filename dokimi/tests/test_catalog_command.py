# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI coverage for `catalog check` against external skill/task catalogs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.utils import InputError
from dokimi.cli import build_parser

_DEEP_FRONTMATTER = b"---\nname: " + b"[" * 100000 + b"\n---\nbody\n"


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for {name} things.\n---\n# {name}\n",
        encoding="utf-8",
    )


def _write_task(root: Path, name: str) -> None:
    task_dir = root / "evaluation" / "harbor" / "tasks" / name
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "solution").mkdir()
    (task_dir / "instruction.md").write_text("Do the thing.", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        '[metadata]\ndifficulty = "easy"\n\n[agent]\ntimeout_sec = 60\n\n'
        '[verifier]\ntimeout_sec = 10\n\n[environment]\n',
        encoding="utf-8",
    )


def _write_manifests(root: Path) -> None:
    (root / "skills.yaml").write_text(
        'schema_version: "1"\n'
        'skills:\n'
        '  - name: "uxl-onetbb"\n'
        '    status: "incubating"\n'
        '    catalog_path: "skills/uxl-onetbb"\n',
        encoding="utf-8",
    )
    (root / "evaluation" / "harbor").mkdir(parents=True, exist_ok=True)
    (root / "evaluation" / "harbor" / "suites.json").write_text(
        """
        {
          "schema_version": "1.0",
          "suites": [
            {
              "skill": "uxl-onetbb",
              "tasks": [
                {"name": "onetbb-real-task", "status": "implemented", "track": "executable"},
                {"name": "onetbb-planned-task", "status": "planned", "track": "executable"}
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )


def test_catalog_scan_reports_skills_and_tasks(tmp_path, capsys):
    _write_skill(tmp_path, "uxl-onetbb")
    _write_task(tmp_path, "onetbb-real-task")
    _write_manifests(tmp_path)

    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path)])
    args.func(args)

    out = capsys.readouterr().out
    assert "uxl-onetbb" in out
    assert "onetbb-real-task" in out
    assert "ok" in out
    assert "skipped" in out  # onetbb-planned-task has no task.toml on disk


def test_catalog_scan_kind_filters_to_skills_only(tmp_path, capsys):
    _write_skill(tmp_path, "uxl-onetbb")
    _write_task(tmp_path, "onetbb-real-task")
    _write_manifests(tmp_path)

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]
    )
    args.func(args)

    out = capsys.readouterr().out
    assert "uxl-onetbb" in out
    assert "onetbb-real-task" not in out


def test_catalog_scan_exits_nonzero_on_load_error(tmp_path, capsys):
    # Manifest declares a skill whose catalog_path doesn't exist -> real error,
    # not a benign "planned"/non-executable skip.
    (tmp_path / "skills.yaml").write_text(
        'schema_version: "1"\n'
        'skills:\n'
        '  - name: "uxl-ghost"\n'
        '    status: "incubating"\n'
        '    catalog_path: "skills/does-not-exist"\n',
        encoding="utf-8",
    )

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]
    )
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1


@pytest.mark.parametrize("broken", ["dangling-symlink", "directory"])
def test_catalog_scan_exits_nonzero_on_a_skill_md_that_is_not_a_file(tmp_path, capsys, broken):
    # discover_skills leaves such a SKILL.md out; the bare scan must still report it.
    _write_skill(tmp_path, "good")
    bad = tmp_path / "skills" / "bad" / "SKILL.md"
    bad.parent.mkdir(parents=True)
    if broken == "directory":
        bad.mkdir()
    else:
        try:
            bad.symlink_to(tmp_path / "missing.md")
        except OSError:  # Windows without Developer Mode or the symlink privilege
            pytest.skip("this OS refuses an unprivileged symlink")

    parser = build_parser()
    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    assert "bad" in capsys.readouterr().out


def test_catalog_scan_exits_nonzero_on_a_task_that_exists_but_cannot_load(tmp_path, capsys):
    # The tasks-half sibling of the test above: `catalog check` reported a broken catalog as clean
    # because no test exercised the tasks-half exit branch.
    _write_task(tmp_path, "onetbb-broken-task")
    (tmp_path / "evaluation/harbor/tasks/onetbb-broken-task/task.toml").write_text(
        "[metadata\ndifficulty = \"easy\"\n", encoding="utf-8"
    )
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        '{"suites": [{"skill": "x", "tasks": ['
        '{"name": "onetbb-broken-task", "status": "implemented", "track": "executable"}'
        ']}]}',
        encoding="utf-8",
    )

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"]
    )
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "ERROR" in out
    assert "skipped" not in out


def test_catalog_scan_exits_zero_when_every_not_loaded_entry_is_a_benign_skip(tmp_path, capsys):
    # The guard against over-correcting: a catalog whose only non-loading
    # entries are `status: "planned"` and a non-executable track is exactly what
    # uxlfoundation/skills looks like today (43 of its 49 declared tasks), and it
    # must still exit 0 or the new CI smoke job reddens on a healthy upstream.
    _write_task(tmp_path, "onetbb-answer-quality-task")
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(
        '{"suites": [{"skill": "x", "tasks": ['
        '{"name": "onetbb-planned-task", "status": "planned", "track": "executable"},'
        '{"name": "onetbb-answer-quality-task", "status": "implemented",'
        ' "track": "answer-quality"}'
        ']}]}',
        encoding="utf-8",
    )

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"]
    )
    args.func(args)  # no SystemExit

    out = capsys.readouterr().out
    assert "skipped" in out
    assert "ERROR" not in out


def test_catalog_scan_on_a_root_with_no_skill_md_says_so_and_exits_1(tmp_path, capsys):
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)

    assert exc_info.value.code == 1
    assert f"no SKILL.md found under {tmp_path}" in capsys.readouterr().err


def test_catalog_scan_with_a_manifest_that_declares_no_skill_says_so_and_exits_1(tmp_path, capsys):
    _write_skill(tmp_path, "lint")
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert f"skills.yaml under {tmp_path} has no skill that can be scanned" in err
    assert "no SKILL.md found" not in err


def test_catalog_scan_with_tasks_but_no_skill_md_warns_and_exits_0_unless_strict(tmp_path, capsys):
    _write_task(tmp_path, "onetbb-real-task")
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path)])
    args.func(args)  # no SystemExit: the tasks are a catalog the scan can run

    assert f"no SKILL.md found under {tmp_path}" in capsys.readouterr().err
    strict = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--strict"])
    with pytest.raises(SystemExit) as exc_info:
        strict.func(strict)
    assert exc_info.value.code == 1


def test_catalog_scan_kind_tasks_does_not_ask_for_a_skill_md(tmp_path, capsys):
    _write_task(tmp_path, "onetbb-real-task")
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"])
    args.func(args)

    assert "no SKILL.md" not in capsys.readouterr().err


def test_catalog_scan_rejects_missing_root(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path / "does-not-exist")]
    )
    with pytest.raises(InputError):
        args.func(args)


def test_catalog_scan_reports_clean_error_on_malformed_manifest(tmp_path):
    # A syntax error in the external repo's own manifest must produce a
    # readable CLI error, not a raw traceback.
    (tmp_path / "skills.yaml").write_text("skills: [this is not: valid: yaml:", encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]
    )
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert "invalid skills.yaml" in str(exc_info.value)


def test_catalog_scan_reports_clean_error_on_non_utf8_skills_manifest(tmp_path):
    (tmp_path / "skills.yaml").write_bytes(b"skills: [\xff\xfe]\n")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert "invalid skills.yaml" in str(exc_info.value)


@pytest.mark.parametrize(
    ("kind", "manifest", "content"),
    [
        ("skills", "skills.yaml", "skills: []\n"),
        ("tasks", "evaluation/harbor/suites.json", '{"suites": []}'),
    ],
)
def test_catalog_scan_reports_clean_error_on_unreadable_manifest(tmp_path, monkeypatch, kind, manifest, content):
    path = tmp_path / manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    real_read_text = Path.read_text

    def read_text(self, *a, **kw):
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", kind])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert f"cannot read {path.name} at {path}" in str(exc_info.value)


def test_catalog_scan_reports_clean_error_on_non_utf8_suites_manifest(tmp_path):
    manifest = tmp_path / "evaluation" / "harbor" / "suites.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"suites": ["\xff"]}')

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert "invalid suites.json" in str(exc_info.value)


@pytest.mark.parametrize("text", ["[]", "null", '{"suites": 5}', '{"suites": {}}', '{"suites": 0}',
                                  '{"suites": [{"tasks": {}}]}', '{"suites": [1]}', '{"suites": [{"tasks": [3]}]}',
                                  '{"suites": [{"tasks": 3}]}', pytest.param("[" * 200000, id="deeply-nested")])
def test_catalog_scan_reports_clean_error_on_wrong_shaped_suites_manifest(tmp_path, text):
    manifest = tmp_path / "evaluation" / "harbor" / "suites.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert f"invalid suites.json at {manifest}" in str(exc_info.value)


@pytest.mark.parametrize("text", ["{}", '{"suites": null}', '{"suites": [{"tasks": null}]}'])
def test_catalog_scan_reads_a_null_or_missing_suites_manifest_list_as_empty(tmp_path, capsys, text):
    (tmp_path / "evaluation" / "harbor" / "tasks").mkdir(parents=True)
    (tmp_path / "evaluation" / "harbor" / "suites.json").write_text(text, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"])
    args.func(args)
    assert f"# tasks (0 declared under {tmp_path})" in capsys.readouterr().out


def test_catalog_scan_reports_clean_error_on_deeply_nested_skills_manifest(tmp_path):
    (tmp_path / "skills.yaml").write_text("[" * 200000, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert f"invalid skills.yaml at {tmp_path / 'skills.yaml'}" in str(exc_info.value)


def test_catalog_scan_names_the_manifest_when_a_value_cannot_be_built(tmp_path):
    (tmp_path / "skills.yaml").write_text("- name: 2026-99-99\n", encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert str(exc_info.value).startswith(f"skills.yaml: invalid skills.yaml at {tmp_path / 'skills.yaml'}: ")


@pytest.mark.parametrize(
    "content",
    [None, b"\xff\xfe", _DEEP_FRONTMATTER, b"---\nname: 2026-99-99\n---\nbody\n"],
    ids=["directory", "non-utf8", "deep-frontmatter", "bad-date-frontmatter"],
)
def test_catalog_scan_reports_a_malformed_skill_as_an_error_row(tmp_path, capsys, content):
    skill_md = tmp_path / "broken" / "SKILL.md"
    skill_md.parent.mkdir()
    if content is None:
        skill_md.mkdir()
    else:
        skill_md.write_bytes(content)

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    assert f"broken\t-\t{skill_md.parent}\tERROR:" in capsys.readouterr().out


def test_catalog_scan_reports_a_deep_manifest_listed_skill_as_an_error_row(tmp_path, capsys):
    _write_skill(tmp_path, "good")
    deep = tmp_path / "skills" / "deep"
    deep.mkdir()
    (deep / "SKILL.md").write_bytes(_DEEP_FRONTMATTER)
    (tmp_path / "skills.yaml").write_text("- name: good\n- name: deep\n", encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert f"deep\t-\t{deep}\tERROR:" in out
    assert f"good\t-\t{tmp_path / 'skills' / 'good'}\tok" in out


def test_catalog_scan_reports_a_task_name_the_filesystem_rejects(tmp_path, monkeypatch):
    from dokimi.external import catalog as cat

    manifest = tmp_path / "evaluation" / "harbor" / "suites.json"
    manifest.parent.mkdir(parents=True)
    name = "a" * 300
    manifest.write_text(json.dumps({"suites": [{"tasks": [{"name": name, "track": "executable"}]}]}), encoding="utf-8")

    def too_long(path):
        raise OSError(36, "File name too long", str(path))

    monkeypatch.setattr(cat, "is_task_dir", too_long)
    entries = cat.read_task_manifest(tmp_path)
    assert entries[0].failed and "File name too long" in entries[0].reason


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe", b"---\nname: a\n---\nbody\n", _DEEP_FRONTMATTER],
    ids=["non-utf8", "missing-description", "deep-frontmatter"],
)
def test_catalog_trigger_set_reports_clean_error_on_malformed_skill_md(tmp_path, content):
    catalog = tmp_path / "skills"
    (catalog / "a").mkdir(parents=True)
    (catalog / "a" / "SKILL.md").write_bytes(content)
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "hi"}]), encoding="utf-8")

    args = build_parser().parse_args(["eval", "triggers", "generate", "--catalog", str(catalog),
                                      "--questions", f"{questions}=none", "--out", str(tmp_path / "o.json")])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert f"--catalog {catalog}:" in str(exc_info.value)


@pytest.mark.parametrize(
    ("instruction", "task_toml"),
    [(None, ""), ("Do the thing.", "[metadata\n")],
    ids=["missing-instruction", "unparsable-task-toml"],
)
def test_catalog_scan_reports_clean_error_on_unloadable_task_dir(tmp_path, instruction, task_toml):
    task = tmp_path / "evaluation" / "harbor" / "tasks" / "t"
    task.mkdir(parents=True)
    (task / "task.toml").write_text(task_toml, encoding="utf-8")
    if instruction is not None:
        (task / "instruction.md").write_text(instruction, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(InputError) as exc_info:
        args.func(args)
    assert "cannot read tasks at" in str(exc_info.value)


def _check_rows(skills: Path, capsys) -> dict:
    args = build_parser().parse_args(
        ["catalog", "check", "--root", str(skills), "--kind", "skills", "--format", "json"])
    try:
        args.func(args)
    except SystemExit:  # a skill that fails to load exits 1; the rows are printed either way
        pass
    return {row["name"]: row for row in json.loads(capsys.readouterr().out)["skills"]}


def test_catalog_check_reports_body_listing_fallback(tmp_path, capsys):
    """#1087: a row previews the entry `repo budget` charges, including the body fallback of a skill that fails to load."""
    skills = tmp_path / ".claude" / "skills"
    fixtures = {
        "first-line": ("---\nname: first-line\n---\n  Sort café logs.  \nIgnore this line.\n",
                       "Sort café logs.", "body", 15, 25),
        "leading-blank": ("---\nname: leading-blank\n---\n\n  \nSummarize queues.\nIgnore this line.\n",
                          "Summarize queues.", "body", 17, 30),
        "explicit": ("---\nname: explicit\ndescription: Use when tagging notes.\n---\nDifferent body text.\n",
                     "Use when tagging notes.", "frontmatter", 23, 31),
        "with-when": ("---\nname: with-when\ndescription: Tag notes.\nwhen_to_use: on save\n---\nBody.\n",
                      "Tag notes.", "frontmatter", 20, 29),
    }
    for name, (content, *_rest) in fixtures.items():
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(content, encoding="utf-8")
    rows = _check_rows(skills, capsys)
    for name, (_, description, source, description_chars, listing_chars) in fixtures.items():
        assert rows[name]["listing"] == {"description": description, "source": source,
                                         "description_chars": description_chars,
                                         "listing_chars": listing_chars}
    assert rows["first-line"]["state"] == "error"
    assert rows["first-line"]["listing_chars"] == 25
    assert rows["leading-blank"]["listing_chars"] == 30


def test_catalog_check_listing_is_null_without_text(tmp_path, capsys):
    """#1087: no description and an empty body leaves nothing to list, so `listing` is null."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "empty").mkdir(parents=True)
    (skills / "empty" / "SKILL.md").write_text("---\nname: empty\n---\n\n  \n", encoding="utf-8")
    assert _check_rows(skills, capsys)["empty"]["listing"] is None


def test_catalog_check_listing_caps_the_charge_not_the_length(tmp_path, capsys):
    """#1087: `listing_chars` stops at 1,536 characters of text; `description_chars` is the uncapped length."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "long").mkdir(parents=True)
    (skills / "long" / "SKILL.md").write_text(
        f"---\nname: long\ndescription: {'x' * 1600}\n---\nBody.\n", encoding="utf-8")
    listing = _check_rows(skills, capsys)["long"]["listing"]
    assert (listing["description_chars"], listing["listing_chars"]) == (1600, 4 + 1536)


def test_catalog_check_unlisted_skill_without_description_has_no_listing_chars(tmp_path, capsys):
    """#1087: a skill without `description` that sets `disable-model-invocation` is not listed, so `listing_chars` stays null."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "hidden").mkdir(parents=True)
    (skills / "hidden" / "SKILL.md").write_text(
        "---\nname: hidden\ndisable-model-invocation: true\n---\nSort logs.\n", encoding="utf-8")
    row = _check_rows(skills, capsys)["hidden"]
    assert row["listing_chars"] is None
    assert row["listing"]["listing_chars"] == len("hidden") + len("Sort logs.")


def test_catalog_check_opencode_skill_without_description_has_no_listing_chars(tmp_path, capsys):
    """#1087: OpenCode does not advertise a skill without `description`, so it adds nothing to `listing_chars`."""
    skills = tmp_path / ".opencode" / "skills"
    (skills / "quiet").mkdir(parents=True)
    (skills / "quiet" / "SKILL.md").write_text("---\nname: quiet\n---\n# Quiet\n", encoding="utf-8")
    assert _check_rows(skills, capsys)["quiet"]["listing_chars"] is None


def test_catalog_check_previews_a_manifest_entry_pointing_at_skill_md(tmp_path, capsys):
    """#1087: a `catalog_path` that names `SKILL.md` itself gets the same preview and charge as its directory."""
    (tmp_path / "skills" / "quiet").mkdir(parents=True)
    (tmp_path / "skills" / "quiet" / "SKILL.md").write_text("---\nname: quiet\n---\nSort logs.\n", encoding="utf-8")
    (tmp_path / "skills.yaml").write_text(
        "skills:\n  - name: quiet\n    catalog_path: skills/quiet/SKILL.md\n", encoding="utf-8")
    row = _check_rows(tmp_path, capsys)["quiet"]
    assert row["listing"] is not None
    assert (row["listing"]["listing_chars"], row["listing_chars"]) == (15, 15)


def test_catalog_check_whitespace_description_is_charged_like_repo_budget(tmp_path, capsys):
    """#1087: a blank `description` fails the load as a missing one and `repo budget` charges it, so `listing_chars` does too."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "quiet").mkdir(parents=True)
    (skills / "quiet" / "SKILL.md").write_text(
        "---\nname: quiet\ndescription: '   '\n---\nSort logs.\n", encoding="utf-8")
    row = _check_rows(skills, capsys)["quiet"]
    assert row["listing"]["listing_chars"] == 8
    assert row["listing_chars"] == 8


def test_catalog_check_skill_with_a_bad_name_and_no_description_has_no_listing_chars(tmp_path, capsys):
    """#1087: the body fallback charges only a skill whose sole fault is the description; a name that breaks the spec leaves it unlisted."""
    skills = tmp_path / ".claude" / "skills"
    (skills / "quiet").mkdir(parents=True)
    (skills / "quiet" / "SKILL.md").write_text("---\nname: WRONG\n---\nSort logs.\n", encoding="utf-8")
    row = _check_rows(skills, capsys)["quiet"]
    assert row["listing_chars"] is None
    assert row["listing"]["listing_chars"] == len("WRONG") + len("Sort logs.")


def test_catalog_check_symlinked_skill_md_is_named_by_its_own_directory(tmp_path, capsys):
    """#1087: a `SKILL.md` symlink without `name` is charged under the directory it is listed in, as `repo budget` does."""
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "entry.md").write_text("---\n---\nSort logs.\n", encoding="utf-8")
    skills = tmp_path / ".claude" / "skills"
    (skills / "alias").mkdir(parents=True)
    (skills / "alias" / "SKILL.md").symlink_to("../../../shared/entry.md")
    row = _check_rows(tmp_path, capsys)["alias"]
    assert row["listing"]["listing_chars"] == len("alias") + len("Sort logs.")
    assert row["listing_chars"] == len("alias") + len("Sort logs.")


def _check_report(root: Path, capsys) -> dict:
    args = build_parser().parse_args(["catalog", "check", "--root", str(root), "--kind", "skills", "--format", "json"])
    try:
        args.func(args)
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def _write_quiet(path: Path, body: str = "Sort logs.") -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: quiet\n---\n{body}\n", encoding="utf-8")


def test_catalog_check_counts_a_mirror_without_description_once(tmp_path, capsys):
    """#1087, docs/catalog-jsonl.md `listing`: byte-identical copies of a skill charged for a missing `description` are one entry."""
    _write_quiet(tmp_path / "skills" / "quiet")
    _write_quiet(tmp_path / "plugins" / "x" / "skills" / "quiet")
    report = _check_report(tmp_path, capsys)
    assert [row["listing_chars"] for row in report["skills"]] == [15, 15]
    assert report["listing"]["chars"] == 15


def test_catalog_check_drops_conflicting_names_without_description(tmp_path, capsys):
    """#1087, docs/catalog-jsonl.md `listing`: a name whose copies differ is left out of the total, charged fallback rows included."""
    _write_quiet(tmp_path / "skills" / "quiet")
    _write_quiet(tmp_path / "plugins" / "x" / "skills" / "quiet", "Sort other logs.")
    assert _check_report(tmp_path, capsys)["listing"]["chars"] == 0


def test_catalog_check_manifest_entry_must_name_skill_md(tmp_path, capsys):
    """#1087: a `catalog_path` naming `README.md` is not a skill file, so it has no preview and no charge."""
    (tmp_path / "skills" / "quiet").mkdir(parents=True)
    (tmp_path / "skills" / "quiet" / "README.md").write_text("---\nname: quiet\n---\nSort logs.\n", encoding="utf-8")
    (tmp_path / "skills.yaml").write_text(
        "skills:\n  - name: quiet\n    catalog_path: skills/quiet/README.md\n", encoding="utf-8")
    row = _check_rows(tmp_path, capsys)["quiet"]
    assert (row["listing"], row["listing_chars"]) == (None, None)


def test_catalog_check_name_mismatch_is_read_from_the_listed_directory(tmp_path, capsys):
    """#1087: a `SKILL.md` symlinked to a root-level file is not at the root, so a declared name that is not its directory fails `name-mismatch` and is not charged."""
    (tmp_path / "entry.md").write_text("---\nname: other\n---\nSort logs.\n", encoding="utf-8")
    (tmp_path / "skills" / "quiet").mkdir(parents=True)
    (tmp_path / "skills" / "quiet" / "SKILL.md").symlink_to("../../entry.md")
    row = next(iter(_check_rows(tmp_path, capsys).values()))
    assert row["listing_chars"] is None
    assert row["listing"]["listing_chars"] == len("other") + len("Sort logs.")
