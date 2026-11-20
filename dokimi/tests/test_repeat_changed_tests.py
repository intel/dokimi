# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/repeat_changed_tests.py, CI's repeat of the test files a PR changes (#1035)."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "repeat_changed_tests", PROJECT / "scripts" / "repeat_changed_tests.py")
assert _spec and _spec.loader
rct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rct)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _put(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """A repository whose ``main`` holds a test file to modify, one to delete, one to rename."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _put(tmp_path, "dokimi/tests/test_kept.py", "a = 1\n" * 20)
    _put(tmp_path, "dokimi/tests/test_deleted.py")
    _put(tmp_path, "dokimi/tests/test_moved.py", "b = 2\n" * 20)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    return tmp_path


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")


def test_added_modified_and_renamed_test_files_are_listed_and_deleted_ones_are_not(repo):
    _put(repo, "dokimi/tests/test_new.py", "n = 3\n" * 20)
    _put(repo, "dokimi/tests/test_kept.py", "a = 1\n" * 20 + "c = 3\n")
    (repo / "dokimi/tests/test_deleted.py").unlink()
    (repo / "dokimi/tests/test_moved.py").rename(repo / "dokimi/tests/test_renamed.py")
    _commit(repo)
    assert rct.changed_test_files("main", "HEAD", repo) == [
        "tests/test_kept.py", "tests/test_new.py", "tests/test_renamed.py"]


def test_only_test_files_directly_under_tests_count(repo):
    _put(repo, "dokimi/tests/helper.py")
    _put(repo, "dokimi/tests/data/test_data.py")
    _put(repo, "dokimi/tests/test_pkg/test_nested.py")
    _put(repo, "dokimi/dokimi/test_inside_package.py")
    _put(repo, "dokimi/scripts/test_script.py")
    _put(repo, "dokimi/docs/quickstart.md")
    _commit(repo)
    assert rct.changed_test_files("main", "HEAD", repo) == []


def test_a_bad_base_raises_so_main_can_exit_2(repo):
    with pytest.raises(subprocess.CalledProcessError):
        rct.changed_test_files("no-such-ref", "HEAD", repo)
    assert rct.main(["--base", "no-such-ref"]) == 2


def test_no_changed_file_runs_nothing(capsys):
    calls: list[int] = []

    def runner(_files, seed):
        calls.append(seed)
        return 0

    assert rct.repeat([], 20, 10, runner=runner) == 0
    assert calls == []
    assert "nothing to repeat" in capsys.readouterr().out


def test_every_seed_runs_once_with_the_same_files_when_all_pass():
    seen: list[tuple[tuple[str, ...], int]] = []

    def runner(files, seed):
        seen.append((tuple(files), seed))
        return 0

    assert rct.repeat(["tests/test_a.py", "tests/test_b.py"], 5, 10, runner=runner) == 0
    assert seen == [(("tests/test_a.py", "tests/test_b.py"), seed) for seed in (1, 2, 3, 4, 5)]


def test_the_first_failing_seed_stops_the_run_and_is_named(capsys):
    seeds: list[int] = []

    def runner(_files, seed):
        seeds.append(seed)
        return 1 if seed == 3 else 0

    assert rct.repeat(["tests/test_a.py"], 20, 10, runner=runner) == 1
    assert seeds == [1, 2, 3]
    assert "replay with `pytest -n auto --randomly-seed=3 tests/test_a.py`" in capsys.readouterr().err


def test_files_past_the_cap_are_logged_and_not_repeated(capsys):
    names = [f"tests/test_{i:02d}.py" for i in range(12)]
    seen: list[tuple[str, ...]] = []

    def runner(files, _seed):
        seen.append(tuple(files))
        return 0

    assert rct.repeat(names, 2, 10, runner=runner) == 0
    assert all(batch == tuple(names[:10]) for batch in seen)
    out = capsys.readouterr().out
    assert "2 more changed test file(s) over the cap of 10" in out
    assert "tests/test_10.py tests/test_11.py" in out
