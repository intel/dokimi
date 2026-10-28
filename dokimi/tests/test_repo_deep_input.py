# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo` commands exit 1 with a message on deeply nested or non-UTF-8 manifest, static and harness files."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from dokimi.repos.harness_profile import ProfileError, load_profile
from dokimi.repos.manifest import ManifestError, load_manifest
from dokimi.repos.source import RepoSource, open_source

DEEP = "[" * 100000


def _run(*argv: str) -> None:
    args = build_parser().parse_args(["repo", *argv])
    args.func(args)


def _exits_1_naming(capsys, path, *argv: str) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(*argv)
    assert exc.value.code == 1
    assert str(path) in capsys.readouterr().err


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".agent-eval").mkdir(parents=True)
    return root


@pytest.mark.parametrize("command", [["scan"], ["validate"], ["reach", "--model", "m", "--dry-run"]])
def test_a_deeply_nested_manifest_file_exits_1_naming_it(repo, tmp_path, capsys, command):
    manifest = tmp_path / "deep.yaml"
    manifest.write_text(DEEP, encoding="utf-8")
    _exits_1_naming(capsys, manifest, command[0], str(repo), "--manifest-file", str(manifest), *command[1:])


def test_a_deeply_nested_static_file_exits_1(repo, tmp_path, capsys):
    static = tmp_path / "static.json"
    static.write_text(DEEP, encoding="utf-8")
    manifest = tmp_path / "m.yaml"
    manifest.write_text("schema: repo_manifest.v1\nid: toy\nguidance:\n  globs: ['AGENTS.md']\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run("scan", str(repo), "--manifest-file", str(manifest), "--static", str(static))
    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("✗ ")


@pytest.mark.parametrize("command", [["budget"], ["reach", "--model", "m", "--dry-run"]])
def test_a_deeply_nested_harness_file_exits_1_naming_it(repo, tmp_path, capsys, command):
    profile = tmp_path / "profile.yaml"
    profile.write_text(DEEP, encoding="utf-8")
    _exits_1_naming(capsys, profile, command[0], str(repo), "--harness", str(profile), *command[1:])


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
def test_a_non_utf8_repo_manifest_exits_1(repo, capsys, command):
    (repo / ".agent-eval" / "repo-eval.yaml").write_bytes(b"\xff\xfe\x00bad")
    _exits_1_naming(capsys, "repo-eval.yaml", command[0], str(repo), *command[1:])
