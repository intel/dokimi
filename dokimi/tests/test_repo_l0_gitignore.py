# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The L0 ``.gitignore`` matcher agrees with ``git check-ignore`` on the same files."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from dokimi.repos.l0.gitignore import GitIgnore

IGNORES = {
    ".gitignore": "\n".join([
        "# a comment, then a blank line",
        "",
        "*.o",
        "!keep.o",
        "build/",
        "!build/keep.txt",
        "/top.txt",
        "docs/**/gen",
        "**/cache",
        "logs/**",
        "a/**/b",
        "data/*.csv",
        r"\#hash",
        "[abc].tmp",
        "[!x]y.bin",
        "include/*",
        "[z-a].h",  # a reversed range keeps only its start
        "[[:digit:]].d",
        r"[\]]x.e",
        "[a/b]x",  # a bracket's / does not split the pattern
        "d[!a]e/f",  # nor does a bracket match one
        "[]a]q",
        "a[",  # unclosed: matches nothing
        r"\!bang",
        "sp\\ ",  # an escaped trailing space is kept
        "[^x]z.bin",
        "[a-]w",
        "[[:digit:]]9.d",
        "",
    ]),
    "sub/.gitignore": "!*.o\nlocal\n",
}

PATHS = [
    "x.o", "deep/x.o", "keep.o", "deep/keep.o", "sub/x.o", "sub/deeper/x.o",
    "build/a/b.c", "src/build/x.c", "build/keep.txt", "notbuild/x.c",
    "top.txt", "sub/top.txt",
    "docs/gen", "docs/a/b/gen", "other/docs/gen",
    "cache", "x/cache/y.txt",
    "logs/a/b.txt", "logs.txt",
    "a/b", "a/x/y/b", "a/bb",
    "data/x.csv", "data/sub/x.csv",
    "#hash", "hash",
    "a.tmp", "d.tmp",
    "ay.bin", "xy.bin",
    "include/x.h", "sub/include/x.h",
    "z.h", "y.h", "1.d", "a.d", "]x.e", "ax", "bx", "sub/ax", "a[",
    "d/e/f", "dbe/f", "]q", "aq",
    "sub/local", "local", "sub/deeper/local",
    "!bang", "sp ", "sp", "yz.bin", "xz.bin", "-w", "aw", "99.d",
]


def _git_ignored(root: Path) -> set[str]:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    out = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "--", *PATHS],
                         capture_output=True, text=True, env=env, check=False)
    assert out.returncode in (0, 1), out.stderr  # 1: nothing ignored
    return set(out.stdout.split("\n")) - {""}


def test_the_matcher_ignores_what_git_ignores(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in IGNORES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    for rel in PATHS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")

    rules = GitIgnore(IGNORES, IGNORES.__getitem__)
    ours = {p for p in PATHS if rules.ignored_by(p) is not None}
    assert ours == _git_ignored(tmp_path)


@pytest.mark.parametrize("path, source, line", [
    ("deep/x.o", ".gitignore", 3),
    # Nothing under an ignored directory comes back, so the directory's line is the reason.
    ("build/keep.txt", ".gitignore", 5),
    ("sub/deeper/local", "sub/.gitignore", 2),
])
def test_the_rule_that_ignores_a_path_is_named(path, source, line):
    rule = GitIgnore(IGNORES, IGNORES.__getitem__).ignored_by(path)
    assert rule is not None and (rule.source, rule.line) == (source, line)


def test_a_directory_only_rule_needs_a_directory():
    rules = GitIgnore({".gitignore": "out/\n"}, lambda _: "out/\n")
    assert rules.ignored_by("out") is None
    assert rules.ignored_by("out", is_dir=True) is not None


@pytest.mark.parametrize("line, path", [("x\\", "x\\"), ("[[:foo:]]x", "f]x"), ("[a-\\", "[a-\\")])
def test_a_line_git_cannot_read_matches_nothing(line, path):
    assert GitIgnore({".gitignore": line}, lambda _: line).ignored_by(path) is None


def test_many_stars_do_not_backtrack():
    # A backtracking [^/]* per star takes ~15 s here; the line and the path are repository content.
    line = "*a" * 9 + "*b"
    rules = GitIgnore({".gitignore": line}, lambda _: line)
    start = time.perf_counter()
    assert rules.ignored_by("pkg/" + "a" * 45) is None
    assert rules.ignored_by("pkg/" + "a" * 45 + "b") is not None
    assert time.perf_counter() - start < 1
