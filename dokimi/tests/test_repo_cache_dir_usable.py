# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`--cache-dir` of the `repo` commands is checked before any clone (#765): a bad value exits 2 naming the flag."""

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from dokimi import cli
from dokimi.commands.repo import _cache_dir
from tests.cli_refusal import refusal

PIN = "HEAD"


def _argv(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])


def _source(tmp_path) -> str:
    return f"file://{tmp_path}/nothere@{PIN}"


def _commands(tmp_path):
    return {
        "fetch": ["repo", "fetch", _source(tmp_path)],
        "scan": ["repo", "scan", _source(tmp_path)],
        "reach": ["repo", "reach", _source(tmp_path), "--model", "m"],
    }


@pytest.mark.parametrize("command", ["fetch", "scan", "reach"])
def test_repo_command_refuses_a_cache_dir_that_is_a_regular_file(command, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cachefile"
    cache.write_text("x", encoding="utf-8")
    _argv(monkeypatch, *_commands(tmp_path)[command], "--cache-dir", str(cache))
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.code == 2
    assert f"--cache-dir {cache} is not a directory; pass a directory the repository clones can be cached in" in err
    assert "File exists" not in err and cache.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("kind", ["check", "replay"])
def test_eval_commands_that_clone_refuse_a_cache_dir_that_is_a_regular_file(kind, tmp_path, monkeypatch, capsys):
    threads = tmp_path / "threads.json"
    threads.write_text("[]", encoding="utf-8")
    cache = tmp_path / "cachefile"
    cache.write_text("x", encoding="utf-8")
    argv = {"check": ["eval", "check", _source(tmp_path)],
            "replay": ["eval", "tasks", "generate", "--from", "replay", _source(tmp_path), "--threads", str(threads),
                       "--out-dir", str(tmp_path / "out")]}[kind]
    _argv(monkeypatch, *argv, "--cache-dir", str(cache))
    with refusal() as exc:
        cli.main()
    assert exc.code == 2
    assert f"--cache-dir {cache} is not a directory" in capsys.readouterr().err


def test_repo_fetch_refuses_a_child_of_a_regular_file_and_makes_nothing(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    _argv(monkeypatch, *_commands(tmp_path)["fetch"], "--cache-dir", str(blocker / "cache"))
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.code == 2
    assert f"--cache-dir {blocker / 'cache'} has {blocker} above it, which is not a directory" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file"]


def test_repo_fetch_json_reports_the_bad_flag_as_input_not_a_failed_run(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "cachefile"
    cache.write_text("x", encoding="utf-8")
    _argv(monkeypatch, "repo", "fetch", _source(tmp_path), "--cache-dir", str(cache), "--json")
    with refusal() as exc:
        cli.main()
    document = json.loads(capsys.readouterr().out)
    assert exc.code == 2
    assert document["status"] == "failed"
    assert f"--cache-dir {cache} is not a directory" in json.dumps(document)
    assert "exit 1: see stderr" not in json.dumps(document)


def test_repo_scan_of_a_local_directory_also_refuses_a_bad_cache_dir(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cachefile"
    cache.write_text("x", encoding="utf-8")
    _argv(monkeypatch, "repo", "scan", str(repo), "--cache-dir", str(cache))
    with refusal() as exc:
        cli.main()
    assert exc.code == 2
    assert f"--cache-dir {cache} is not a directory" in capsys.readouterr().err


def test_cache_dir_passes_unset_an_existing_directory_and_a_missing_child_unchanged(tmp_path):
    def args(value):
        return argparse.Namespace(cache_dir=value)

    assert _cache_dir(args(None)) is None
    assert _cache_dir(args("")) is None
    assert _cache_dir(args(str(tmp_path))) == tmp_path
    assert _cache_dir(args(str(tmp_path / "new" / "deeper"))) == tmp_path / "new" / "deeper"
    assert not (tmp_path / "new").exists()


def test_repo_fetch_prints_the_sha_of_a_pinned_source_in_a_writable_cache_dir(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a", encoding="utf-8")
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    cache = tmp_path / "cache"
    cache.mkdir()
    _argv(monkeypatch, "repo", "fetch", f"file://{src}@{sha}", "--cache-dir", str(cache))
    cli.main()
    assert capsys.readouterr().out.startswith(f"{sha}\t")
