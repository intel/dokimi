# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI coverage for `catalog scan` against external skill/task catalogs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    args = parser.parse_args(["catalog", "scan", "--root", str(tmp_path)])
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
        ["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"]
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
        ["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"]
    )
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1


def test_catalog_scan_exits_nonzero_on_a_task_that_exists_but_cannot_load(tmp_path, capsys):
    # The tasks-half sibling of the test above. Both halves need their own case:
    # skills failures carry an `error`, task failures a `failed` flag, and the
    # tasks half reached the exit code through a separate branch that nothing
    # exercised -- so `catalog scan` reported a broken catalog as clean.
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
        ["catalog", "scan", "--root", str(tmp_path), "--kind", "tasks"]
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
        ["catalog", "scan", "--root", str(tmp_path), "--kind", "tasks"]
    )
    args.func(args)  # no SystemExit

    out = capsys.readouterr().out
    assert "skipped" in out
    assert "ERROR" not in out


def test_catalog_scan_rejects_missing_root(tmp_path):
    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "scan", "--root", str(tmp_path / "does-not-exist")]
    )
    with pytest.raises(SystemExit):
        args.func(args)


def test_catalog_scan_reports_clean_error_on_malformed_manifest(tmp_path):
    # A syntax error in the external repo's own manifest must produce a
    # readable CLI error, not a raw traceback.
    (tmp_path / "skills.yaml").write_text("skills: [this is not: valid: yaml:", encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(
        ["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"]
    )
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert "invalid skills.yaml" in str(exc_info.value)


def test_catalog_scan_reports_clean_error_on_non_utf8_skills_manifest(tmp_path):
    (tmp_path / "skills.yaml").write_bytes(b"skills: [\xff\xfe]\n")

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
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

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", kind])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert f"cannot read {path.name} at {path}" in str(exc_info.value)


def test_catalog_scan_reports_clean_error_on_non_utf8_suites_manifest(tmp_path):
    manifest = tmp_path / "evaluation" / "harbor" / "suites.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{"suites": ["\xff"]}')

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert "invalid suites.json" in str(exc_info.value)


@pytest.mark.parametrize("text", ["[]", "null", '{"suites": 5}', '{"suites": [1]}', '{"suites": [{"tasks": [3]}]}',
                                  '{"suites": [{"tasks": 3}]}', pytest.param("[" * 200000, id="deeply-nested")])
def test_catalog_scan_reports_clean_error_on_wrong_shaped_suites_manifest(tmp_path, text):
    manifest = tmp_path / "evaluation" / "harbor" / "suites.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(text, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert f"invalid suites.json at {manifest}" in str(exc_info.value)


def test_catalog_scan_reports_clean_error_on_deeply_nested_skills_manifest(tmp_path):
    (tmp_path / "skills.yaml").write_text("[" * 200000, encoding="utf-8")

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert f"invalid skills.yaml at {tmp_path / 'skills.yaml'}" in str(exc_info.value)


@pytest.mark.parametrize(
    "content",
    [None, b"\xff\xfe", _DEEP_FRONTMATTER],
    ids=["directory", "non-utf8", "deep-frontmatter"],
)
def test_catalog_scan_reports_a_malformed_skill_as_an_error_row(tmp_path, capsys, content):
    skill_md = tmp_path / "broken" / "SKILL.md"
    skill_md.parent.mkdir()
    if content is None:
        skill_md.mkdir()
    else:
        skill_md.write_bytes(content)

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "skills"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert exc_info.value.code == 1
    assert f"broken\t-\t{skill_md.parent}\tERROR:" in capsys.readouterr().out


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
    [b"\xff\xfe", b"---\nname: a\n---\nbody\n", _DEEP_FRONTMATTER, None],
    ids=["non-utf8", "missing-description", "deep-frontmatter", "directory"],
)
def test_catalog_trigger_set_reports_clean_error_on_malformed_skill_md(tmp_path, content):
    catalog = tmp_path / "skills"
    (catalog / "a").mkdir(parents=True)
    if content is None:
        (catalog / "a" / "SKILL.md").mkdir()
    else:
        (catalog / "a" / "SKILL.md").write_bytes(content)
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "hi"}]), encoding="utf-8")

    args = build_parser().parse_args(["catalog", "trigger-set", "--catalog", str(catalog),
                                      "--questions", f"{questions}=none", "--out", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit) as exc_info:
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

    args = build_parser().parse_args(["catalog", "scan", "--root", str(tmp_path), "--kind", "tasks"])
    with pytest.raises(SystemExit) as exc_info:
        args.func(args)
    assert "cannot read tasks at" in str(exc_info.value)
