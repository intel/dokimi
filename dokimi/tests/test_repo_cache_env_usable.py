# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`$DOKIMI_REPO_CACHE` is checked like `--cache-dir` before any clone (#827): a bad value exits 2 naming the variable."""

from __future__ import annotations

import argparse
import subprocess

import pytest

from dokimi import cli
from dokimi.commands.repo import _cache_dir
from tests.cli_refusal import refusal

CLOSING = "pass a directory the repository clones can be cached in"


def _argv(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])


def _source(tmp_path) -> str:
    return f"file://{tmp_path}/nothere@HEAD"


def _commands(tmp_path):
    return {
        "fetch": ["repo", "fetch", _source(tmp_path)],
        "scan": ["repo", "scan", _source(tmp_path)],
        "reach": ["repo", "reach", _source(tmp_path), "--model", "m"],
        "check": ["eval", "check", _source(tmp_path)],
    }


def _file(tmp_path):
    path = tmp_path / "cachefile"
    path.write_text("x", encoding="utf-8")
    return path


@pytest.mark.parametrize("command", ["fetch", "scan", "reach", "check"])
def test_repo_command_refuses_a_cache_variable_that_is_a_regular_file(command, tmp_path, monkeypatch, capsys):
    """#827: it was `✗ [Errno 17] File exists: '<file>'`, exit 1, with no mention of the variable."""
    cache = _file(tmp_path)
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(cache))
    _argv(monkeypatch, *_commands(tmp_path)[command])
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.code == 2
    assert f"$DOKIMI_REPO_CACHE {cache} is not a directory; {CLOSING}" in err
    assert "File exists" not in err and cache.read_text(encoding="utf-8") == "x"


def test_repo_fetch_refuses_a_cache_variable_below_a_regular_file_and_makes_nothing(tmp_path, monkeypatch, capsys):
    blocker = _file(tmp_path)
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(blocker / "cache"))
    _argv(monkeypatch, *_commands(tmp_path)["fetch"])
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.code == 2
    assert f"$DOKIMI_REPO_CACHE {blocker / 'cache'} has {blocker} above it, which is not a directory; {CLOSING}" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cachefile"]


@pytest.mark.parametrize("where", ["variable", "flag"])
def test_a_missing_dir_then_dotdot_cannot_hide_a_regular_file(where, tmp_path, monkeypatch, capsys):
    """It passed the check and ended in `[Errno 17] File exists` at the clone: the path is resolved first."""
    blocker = _file(tmp_path)
    sneaky = f"{tmp_path}/missing/../{blocker.name}"
    extra = []
    if where == "variable":
        monkeypatch.setenv("DOKIMI_REPO_CACHE", sneaky)
    else:
        monkeypatch.delenv("DOKIMI_REPO_CACHE", raising=False)
        extra = ["--cache-dir", sneaky]
    _argv(monkeypatch, *_commands(tmp_path)["fetch"], *extra)
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    label = "$DOKIMI_REPO_CACHE" if where == "variable" else "--cache-dir"
    assert exc.code == 2 and f"{label} {blocker} is not a directory; {CLOSING}" in err
    assert "File exists" not in err and not (tmp_path / "missing").exists()


def test_a_cache_dir_flag_wins_over_a_bad_variable(tmp_path, monkeypatch, capsys):
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
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(_file(tmp_path)))
    _argv(monkeypatch, "repo", "fetch", f"file://{src}@{sha}", "--cache-dir", str(cache))
    cli.main()
    assert capsys.readouterr().out.startswith(f"{sha}\t")
    assert any(cache.iterdir())  # cloned into the flag's directory


def test_a_bad_cache_dir_flag_is_still_named_when_the_variable_is_set(tmp_path, monkeypatch, capsys):
    bad = _file(tmp_path)
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(tmp_path))
    _argv(monkeypatch, *_commands(tmp_path)["fetch"], "--cache-dir", str(bad))
    with refusal() as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.code == 2 and f"--cache-dir {bad} is not a directory" in err and "$DOKIMI_REPO_CACHE" not in err


def test_cache_dir_returns_a_good_variable_and_an_empty_one_counts_as_unset(tmp_path, monkeypatch):
    args = argparse.Namespace(cache_dir=None)
    monkeypatch.setenv("DOKIMI_REPO_CACHE", "")
    assert _cache_dir(args) is None
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(tmp_path))
    assert _cache_dir(args) == tmp_path
    assert _cache_dir(argparse.Namespace(cache_dir="")) == tmp_path
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(tmp_path / "new" / "deeper"))
    assert _cache_dir(args) == tmp_path / "new" / "deeper"
    assert not (tmp_path / "new").exists()
