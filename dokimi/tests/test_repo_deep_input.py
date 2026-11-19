# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo` commands exit 2 with a message on deeply nested or non-UTF-8 manifest, static and harness files."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from dokimi.repos.harness_profile import ProfileError, load_profile
from dokimi.repos.manifest import ManifestError, load_manifest
from dokimi.repos.source import RepoSource, open_source
from tests.cli_refusal import refusal

DEEP = "[" * 100000


def _run(*argv: str) -> None:
    args = build_parser().parse_args(["repo", *argv])
    args.func(args)


def _exits_2_naming(capsys, path, *argv: str) -> None:
    with refusal() as exc:
        _run(*argv)
    assert exc.code == 2
    assert str(path) in capsys.readouterr().err


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".agent-eval").mkdir(parents=True)
    return root


@pytest.mark.parametrize("command", [["scan"], ["validate"], ["reach", "--model", "m", "--dry-run"]])
def test_a_deeply_nested_manifest_file_exits_2_naming_it(repo, tmp_path, capsys, command):
    manifest = tmp_path / "deep.yaml"
    manifest.write_text(DEEP, encoding="utf-8")
    _exits_2_naming(capsys, manifest, command[0], str(repo), "--manifest-file", str(manifest), *command[1:])


def test_a_deeply_nested_static_file_exits_2(repo, tmp_path, capsys):
    static = tmp_path / "static.json"
    static.write_text(DEEP, encoding="utf-8")
    manifest = tmp_path / "m.yaml"
    manifest.write_text("schema: repo_manifest.v1\nid: toy\nguidance:\n  globs: ['AGENTS.md']\n", encoding="utf-8")
    with refusal() as exc:
        _run("scan", str(repo), "--manifest-file", str(manifest), "--static", str(static))
    assert exc.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(static) in err


@pytest.mark.parametrize("command", [["budget"], ["reach", "--model", "m", "--dry-run"]])
def test_a_deeply_nested_harness_file_exits_2_naming_it(repo, tmp_path, capsys, command):
    profile = tmp_path / "profile.yaml"
    profile.write_text(DEEP, encoding="utf-8")
    _exits_2_naming(capsys, profile, command[0], str(repo), "--harness", str(profile), *command[1:])


def test_load_profile_reports_a_deeply_nested_file(tmp_path):
    profile = tmp_path / "profile.yaml"
    profile.write_text(DEEP, encoding="utf-8")
    with pytest.raises(ProfileError, match="profile.yaml"):
        load_profile(profile)


@pytest.mark.parametrize("content", [b"\xff\xfe\x00bad", DEEP.encode()])
def test_load_manifest_reports_a_repo_manifest_it_cannot_read(repo, content):
    (repo / ".agent-eval" / "repo-eval.yaml").write_bytes(content)
    tree = open_source(RepoSource.parse(str(repo)), repo.parent / "cache")
    with pytest.raises(ManifestError, match="cannot read"):
        load_manifest(tree)


@pytest.mark.parametrize("command", [["validate"], ["reach", "--model", "m", "--dry-run"]])
def test_a_non_utf8_repo_manifest_exits_2(repo, capsys, command):
    (repo / ".agent-eval" / "repo-eval.yaml").write_bytes(b"\xff\xfe\x00bad")
    _exits_2_naming(capsys, "repo-eval.yaml", command[0], str(repo), *command[1:])


def test_load_manifest_reports_a_repo_manifest_it_has_no_permission_to_read(repo, monkeypatch):
    from dokimi.repos.source import WorkTree

    (repo / ".agent-eval" / "repo-eval.yaml").write_text("id: toy\n", encoding="utf-8")

    def denied(self, path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(WorkTree, "read_text", denied)
    tree = open_source(RepoSource.parse(str(repo)), repo.parent / "cache")
    with pytest.raises(ManifestError, match="cannot read: .*Permission denied"):
        load_manifest(tree)


def test_load_tasks_names_a_task_json_it_cannot_parse(tmp_path):
    """#975: a task.json that is deeply nested, not JSON or not UTF-8 is a PrepError naming the file."""
    from dokimi.repos.manifest import parse_manifest
    from dokimi.repos.workspace import PrepError, load_tasks

    manifest = parse_manifest({"schema": "repo_manifest.v1", "id": "toy", "eval_dir": "ev", "tasks": "ev/tasks",
                               "entry_points": {"grade": "g {run_dir}", "oracle": "o {run_dir}"},
                               "guidance": {"globs": ["AGENTS.md"]}}, "m.yaml")
    for name, content in (("deep", DEEP.encode()), ("bad", b"{"), ("utf8", b"\xff\xfe")):
        task = tmp_path / "tasks" / name
        task.mkdir(parents=True)
        (task / "task.json").write_bytes(content)
        with pytest.raises(PrepError, match=f"{name}.task.json"):
            load_tasks(tmp_path, manifest, [name])


def test_a_deeply_nested_suite_file_is_refused_like_a_bad_one(tmp_path):
    """#975: load_suite refuses a deeply nested suite as `not valid YAML`, as it does a non-UTF-8 one."""
    from dokimi.repos.suite import SuiteError, load_suite

    root = tmp_path / "repo"
    root.mkdir()
    (root / "suite.json").write_text(DEEP + "]" * 100000, encoding="utf-8")
    tree = open_source(RepoSource.parse(str(root)), tmp_path / "cache")
    with pytest.raises(SuiteError, match="not valid YAML"):
        load_suite(tree, "suite.json")


def test_a_manifest_file_that_is_not_a_regular_file_exits_2_naming_it(repo, tmp_path, capsys):
    """#975: a directory or a FIFO as --manifest-file is refused, not read (a FIFO without a writer blocks)."""
    import os

    fifo = tmp_path / "m.yaml"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)
    else:
        fifo.mkdir()
    with refusal() as exc:
        _run("validate", str(repo), "--manifest-file", str(fifo))
    assert exc.code == 2
    err = capsys.readouterr().err
    assert str(fifo) in err and "not a regular file" in err


def test_a_manifest_file_name_too_long_to_stat_exits_2_naming_it(repo, capsys):
    """#975: the regular-file check is inside the OSError handler: an overlong name is a manifest error, not a traceback."""
    name = "0" * 300
    with refusal() as exc:
        _run("validate", str(repo), "--manifest-file", name)
    assert exc.code == 2
    assert name in capsys.readouterr().err
