# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval check`, `repo run` and `eval tasks generate --from replay|prs` exit 2 on a source the user mistyped.

A source `RepoSource.parse` refuses, or a pinned local path inside a repository, is bad input, as with
`--from graph`; a source that parses but cannot be opened (clone, fetch, revision) stays 1.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from dokimi import cli
from tests.cli_refusal import refusal

PIN = "a" * 40
PRS = '[{"number": 7, "reviewThreads": {"nodes": []}, "mergeCommit": null}]'


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    with refusal() as outcome:
        cli.main()
    return outcome.code, capsys.readouterr().err


def _command(name, source, tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(PRS, encoding="utf-8")
    out = str(tmp_path / "out")
    return {
        "check": ["eval", "check", source, "--out", out],
        "run": ["repo", "run", source, "--batch", "b", "--out", out, "--arm", "guidance:none", "--model", "m", "--bin", sys.executable],
        "replay": ["eval", "tasks", "generate", source, "--from", "replay", "--threads", str(prs), "--out", out],
        "prs": ["eval", "tasks", "generate", source, "--from", "prs", "--prs", str(prs), "--out", out],
    }[name]


@pytest.mark.parametrize("name", ["check", "run"])
@pytest.mark.parametrize(("source", "message"), [
    ("{missing}", "Error: local source is not a directory: {missing}"),
    ("https://example.invalid/r", "Error: a remote source must be pinned to a revision: https://example.invalid/r@<sha>"),
])
def test_check_and_run_refuse_a_source_that_does_not_parse_with_exit_2(name, source, message, tmp_path, monkeypatch,
                                                                       capsys):
    missing = str(tmp_path / "missing")
    code, err = _run(monkeypatch, capsys, *_command(name, source.format(missing=missing), tmp_path))
    assert code == 2
    assert err.strip() == message.format(missing=missing)


def _repo_with_sub(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "f.txt").write_text("x", encoding="utf-8")
    # gpgsign is off here: a host with a global commit.gpgsign=true would refuse the commit.
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    return repo, sha


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_pinned_subdirectory_of_a_repository_exits_2(name, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    code, err = _run(monkeypatch, capsys, *_command(name, f"{repo / 'sub'}@{sha}", tmp_path))
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("flag", ["--threads", "--prs"])
@pytest.mark.parametrize("extra", ["missing input", "empty prompt"])
def test_pinned_subdirectory_is_refused_before_the_input_files_are_read(flag, extra, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    source = f"{repo / 'sub'}@{sha}"
    out = str(tmp_path / "out")
    kind = "replay" if flag == "--threads" else "prs"
    argv = ["eval", "tasks", "generate", source, "--from", kind, flag, str(tmp_path / "missing.json"), "--out", out]
    if extra == "empty prompt":
        (tmp_path / "prs.json").write_text(PRS, encoding="utf-8")
        argv[argv.index(str(tmp_path / "missing.json"))] = str(tmp_path / "prs.json")
        argv += ["--prompt-file", str(empty)]
    code, err = _run(monkeypatch, capsys, *argv)
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(("flag", "value"), [("--arm", "guidance:bogus"), ("--bin", "/no/such/bin")])
def test_run_refuses_a_pinned_subdirectory_before_the_arms_and_the_harness(flag, value, tmp_path, monkeypatch,
                                                                           capsys):
    repo, sha = _repo_with_sub(tmp_path)
    argv = _command("run", f"{repo / 'sub'}@{sha}", tmp_path)
    argv[argv.index(flag) + 1] = value
    code, err = _run(monkeypatch, capsys, *argv)
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
@pytest.mark.parametrize(("manifest", "code", "message"), [
    ("../outside.yaml", 2, "Error: path escapes the repository: ../outside.yaml"),
    ("missing.yaml", 1, "✗ missing.yaml: invalid repo_manifest.v1:"),
])
def test_manifest_path_leaving_the_tree_exits_2_and_a_missing_manifest_exits_1(name, manifest, code, message,
                                                                              tmp_path, monkeypatch, capsys):
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "-C", str(tree), "init", "-q"], check=True, capture_output=True)  # `repo run` pins arms in a git dir
    code_got, err = _run(monkeypatch, capsys, *_command(name, str(tree), tmp_path), "--manifest", manifest)
    assert code_got == code
    assert err.strip().splitlines()[0] == message


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_manifest_path_leaving_a_pinned_tree_is_a_missing_manifest_with_exit_1(name, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    code, err = _run(monkeypatch, capsys, *_command(name, f"{repo}@{sha}", tmp_path), "--manifest", "../outside.yaml")
    assert code == 1
    assert err.strip().splitlines()[0] == "✗ ../outside.yaml: invalid repo_manifest.v1:"


@pytest.mark.parametrize("name", ["check", "run"])
def test_check_and_run_keep_exit_1_for_a_pinned_source_that_cannot_be_fetched(name, tmp_path, monkeypatch, capsys):
    from dokimi.repos import SourceError

    def failed(source, cache_dir=None):
        raise SourceError("git fetch failed: unreachable")

    monkeypatch.setattr("dokimi.repos.open_source", failed)
    code, err = _run(monkeypatch, capsys, *_command(name, f"https://127.0.0.1:9/r.git@{PIN}", tmp_path))
    assert code == 1
    assert err.strip() == "✗ git fetch failed: unreachable"
