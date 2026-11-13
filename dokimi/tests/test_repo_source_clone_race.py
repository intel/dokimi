# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Two processes clone one remote source into the shared cache at once (#687): each clone keeps its
own partial directory, and the one that loses the rename uses the winner's clone."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dokimi.repos import source, workspace
from dokimi.repos.source import RepoSource, SourceError, cache_path, ensure_clone, ensure_rev, resolve_rev
from dokimi.repos.workspace import source_git_dir


def _origin(tmp_path: Path) -> tuple[str, str]:
    """A local bare repository with one commit: its ``file://`` URL and the commit's SHA."""
    work, bare = tmp_path / "work", tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (work / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c"],
                   check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    sha = subprocess.run(["git", "-C", str(bare), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    return bare.as_uri(), sha


def test_two_clones_at_once_both_return_the_one_clone(tmp_path, monkeypatch):
    url, sha = _origin(tmp_path)
    real, both_started, one_at_a_time = source._git, threading.Barrier(2, timeout=60), threading.Lock()

    def clone_after_both_checked(git_dir, *args, **kwargs):
        if args[:1] == ("clone",):
            both_started.wait()  # neither has a clone in the cache yet; the second clone runs after the first's rename
            with one_at_a_time:
                return real(git_dir, *args, **kwargs)
        return real(git_dir, *args, **kwargs)

    monkeypatch.setattr(source, "_git", clone_after_both_checked)
    cache = tmp_path / "cache"
    with ThreadPoolExecutor(2) as pool:
        dirs = [f.result() for f in [pool.submit(ensure_clone, url, cache) for _ in range(2)]]
    assert dirs == [cache_path(url, cache)] * 2
    assert resolve_rev(dirs[0], sha) == sha
    assert [p.name for p in cache.iterdir()] == [dirs[0].name]  # the loser's partial clone is removed


def test_a_clone_started_while_another_runs_leaves_the_other_one_alone(tmp_path, monkeypatch):
    url, sha = _origin(tmp_path)
    real, cache, second = source._git, tmp_path / "cache", []

    def second_clone_meanwhile(git_dir, *args, **kwargs):
        out = real(git_dir, *args, **kwargs)
        if args[:1] == ("clone",) and not second:  # the first clone is done and not renamed yet
            second.append(None)
            second[0] = ensure_clone(url, cache)
        return out

    monkeypatch.setattr(source, "_git", second_clone_meanwhile)
    first = ensure_clone(url, cache)
    assert first == second[0] == cache_path(url, cache)
    assert resolve_rev(first, sha) == sha
    assert [p.name for p in cache.iterdir()] == [first.name]


def test_the_full_clone_for_task_bases_leaves_another_one_alone(tmp_path, monkeypatch):
    url, sha = _origin(tmp_path)
    spec, cache, real, second = RepoSource.parse(f"{url}@{sha}"), tmp_path / "cache", workspace._run, []
    tree = source.open_source(spec, cache)

    def second_clone_meanwhile(argv, *args, **kwargs):
        out = real(argv, *args, **kwargs)
        if argv[1:2] == ["clone"] and not second:
            second.append(None)
            second[0] = source_git_dir(spec, tree, cache)
        return out

    monkeypatch.setattr(workspace, "_run", second_clone_meanwhile)
    first = source_git_dir(spec, tree, cache)
    assert first == second[0] == cache_path(url, cache).with_suffix(".full.git")
    assert resolve_rev(first, sha) == sha
    assert sorted(p.name for p in cache.iterdir()) == sorted([first.name, cache_path(url, cache).name])


def test_a_partial_clone_older_than_the_git_timeout_is_removed_and_a_running_one_kept(tmp_path):
    url, _ = _origin(tmp_path)
    cache = tmp_path / "cache"
    name = cache_path(url, cache).name
    dead_old_name, dead, running = (cache / f"{name}.partial", cache / f"{name}.1-aa.partial",
                                    cache / f"{name}.2-bb.partial")
    for d in (dead_old_name, dead, running):
        d.mkdir(parents=True)
    then = time.time() - source._GIT_TIMEOUT_S - 60
    for d in (dead_old_name, dead):
        os.utime(d, (then, then))
    ensure_clone(url, cache)
    assert sorted(p.name for p in cache.iterdir()) == sorted([name, running.name])


def test_a_full_clone_partial_is_aged_by_the_full_clones_own_timeout(tmp_path, monkeypatch):
    url, sha = _origin(tmp_path)
    spec, cache = RepoSource.parse(f"{url}@{sha}"), tmp_path / "cache"
    tree = source.open_source(spec, cache)
    monkeypatch.setattr(workspace, "_GIT_TIMEOUT_S", source._GIT_TIMEOUT_S * 4)  # big full clones get longer
    running = cache / f"{cache_path(url, cache).with_suffix('.full.git').name}.1-aa.partial"
    running.mkdir()
    then = time.time() - source._GIT_TIMEOUT_S * 2
    os.utime(running, (then, then))
    source_git_dir(spec, tree, cache)
    assert running.is_dir()


def _push_topic(tmp_path: Path) -> str:
    """A new commit in the origin on branch ``topic``; its SHA."""
    work = tmp_path / "work"
    (work / "README.md").write_text("moved\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "m"],
                   check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", str(tmp_path / "origin.git"), "HEAD:refs/heads/topic"],
                   check=True)
    return subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def test_a_branch_fetch_that_lost_a_ref_to_another_run_is_run_again(tmp_path, monkeypatch):
    url, _ = _origin(tmp_path)
    git_dir = ensure_clone(url, tmp_path / "cache")
    moved = _push_topic(tmp_path)
    real, held, fetches = source._git, git_dir / "refs" / "heads" / "topic.lock", []

    def other_run_holds_the_ref_once(gd, *args, **kwargs):
        if args[:1] != ("fetch",) or fetches:
            return real(gd, *args, **kwargs)
        fetches.append(args)
        held.parent.mkdir(parents=True, exist_ok=True)
        held.write_text("", encoding="utf-8")  # the other run's fetch is updating refs/heads/topic
        try:
            return real(gd, *args, **kwargs)
        finally:
            held.unlink()

    monkeypatch.setattr(source, "_git", other_run_holds_the_ref_once)
    assert ensure_rev(git_dir, "topic") == moved


def test_a_branch_fetch_failing_for_another_reason_is_not_run_again(tmp_path, monkeypatch):
    url, _ = _origin(tmp_path)
    git_dir = ensure_clone(url, tmp_path / "cache")
    shutil.rmtree(tmp_path / "origin.git")
    real, fetches = source._git, []

    def count_fetches(gd, *args, **kwargs):
        if args[:1] == ("fetch",):
            fetches.append(args)
        return real(gd, *args, **kwargs)

    monkeypatch.setattr(source, "_git", count_fetches)
    with pytest.raises(SourceError, match="git fetch failed"):
        ensure_rev(git_dir, "topic")
    assert len(fetches) == 1
