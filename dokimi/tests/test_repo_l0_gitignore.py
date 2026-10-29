# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The L0 ``.gitignore`` matcher agrees with ``git check-ignore`` on the same files."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
        "tab\t",  # git trims spaces, not tabs
        "bs\\\\ ",  # an escaped backslash does not keep the space after it
        "",
    ]),
    "sub/.gitignore": "!*.o\nlocal\n",
    "crlf/.gitignore": "\ufeffbom\r\nlone\rcr\n",  # git skips the mark, splits at \n only, drops \r before it
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
    "tab\t", "tab", "bs\\", "bs\\ ", "crlf/bom", "crlf/lone\rcr", "crlf/lone", "crlf/cr",
]


# What `git check-ignore` printed for PATHS, so the matcher is checked on a host without git too.
GIT_IGNORED = {
    "x.o", "deep/x.o", "build/a/b.c", "src/build/x.c", "build/keep.txt", "top.txt",
    "docs/gen", "docs/a/b/gen", "cache", "x/cache/y.txt", "logs/a/b.txt", "a/b", "a/x/y/b",
    "data/x.csv", "#hash", "a.tmp", "ay.bin", "include/x.h", "z.h", "1.d", "]x.e", "ax", "bx",
    "dbe/f", "]q", "aq", "sub/local", "sub/deeper/local", "!bang", "sp ", "yz.bin", "-w", "aw",
    "99.d", "tab\t", "bs\\", "crlf/bom", "crlf/lone\rcr",
}


def _git_ignored(root: Path) -> set[str]:
    # HOME and XDG_CONFIG_HOME too: the default core.excludesFile is $XDG_CONFIG_HOME/git/ignore,
    # and git before 2.32 reads $HOME/.gitconfig whatever GIT_CONFIG_GLOBAL says
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "HOME": str(root), "XDG_CONFIG_HOME": str(root)}
    # No template: a host init.templateDir could bring info/exclude rules
    subprocess.run(["git", "init", "-q", "--template=", str(root)], check=True, env=env)
    for rel, text in IGNORES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    # --no-index matches the names alone, so PATHS (a tab, a \r, a trailing space) need no files
    out = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "--stdin", "-z"],
                         input="\0".join(PATHS).encode(), capture_output=True, env=env, check=False)
    assert out.returncode in (0, 1), out.stderr  # 1: nothing ignored
    return set(out.stdout.decode().split("\0")) - {""}  # -z and bytes: names unquoted, a \r kept


def test_the_matcher_ignores_what_git_ignores():
    rules = GitIgnore(IGNORES, IGNORES.__getitem__)
    assert {p for p in PATHS if rules.ignored_by(p) is not None} == GIT_IGNORED


# Windows git may read a \ in a path as a separator, and init sets core.ignorecase there
@pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32",
                    reason="needs a POSIX git")
def test_git_still_ignores_what_the_matcher_is_checked_against(tmp_path):
    assert _git_ignored(tmp_path) == GIT_IGNORED


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


# git check-ignore ignores nothing for each of these lines: none leaves a pattern
@pytest.mark.parametrize("line", ["", " ", "/", "//", "!", "!/"])
def test_a_line_with_no_pattern_is_no_rule(line):
    text = f"gen.h\n{line}\n"
    rules = GitIgnore({".gitignore": text}, lambda _: text)
    assert [r.line for r in rules.rules] == [1]
    assert rules.ignored_by("a/b") is None and rules.ignored_by("a", is_dir=True) is None
    assert rules.reincluded_by("gen.h") is None and rules.names("x", negated=True) == []


def test_a_tree_without_a_gitignore_ignores_nothing():
    rules = GitIgnore(["src/a.py", "README.md"], lambda f: pytest.fail(f"read {f}"))
    assert rules.ignored_by("src/a.py") is None and rules.names("x") == []


def test_many_stars_do_not_backtrack():
    # A backtracking [^/]* per star takes ~15 s here; the line and the path are repository content.
    line = "*a" * 9 + "*b"
    rules = GitIgnore({".gitignore": line}, lambda _: line)
    start = time.perf_counter()
    assert rules.ignored_by("pkg/" + "a" * 45) is None
    assert rules.ignored_by("pkg/" + "a" * 45 + "b") is not None
    assert time.perf_counter() - start < 1


@pytest.mark.parametrize("line, name, path", [
    ("gen.h", "gen.h", "pkg/gen.h"), ("[0-9]*.png", "0x.png", "img/0x.png"),
    ("gen_[a-c].h", "gen_a.h", "gen_a.h"), ("[!x]?.o", "ax.o", "ax.o"), ("[[:upper:]]*", "Ax", "Ax"),
    ("\\*.h", "*.h", "*.h"), ("build/out*/", "outx", "build/outx/f"),
    ("[_]gen.h", "_gen.h", "_gen.h"), ("[-]gen.h", "-gen.h", "-gen.h"), ("[.]gen.h", ".gen.h", ".gen.h"),
])
def test_a_rule_names_a_file_it_ignores(line, name, path):
    rules = GitIgnore({".gitignore": line}, lambda _: line)
    assert rules.names("x") == [name]
    assert rules.ignored_by(path) is not None


def test_a_class_no_candidate_fits_names_nothing():
    line = "[!a-zA-Z0-9_.-]*.o\ngen.h\n"
    assert GitIgnore({".gitignore": line}, lambda _: line).names("x") == ["gen.h"]


def test_a_negation_is_named_only_when_asked_for():
    line = "gen.h\n!keep.o\n"
    rules = GitIgnore({".gitignore": line}, lambda _: line)
    assert rules.names("x") == ["gen.h"]
    assert rules.names("x", negated=True) == ["keep.o"]
