# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""L0 rules ported for parity with oneDAL's own claim checker (issue #41, step D6).

Each case is a set of files and one guidance text, as in ``test_repo_l0_cases.py``: a BUILD
snippet fenced as Python, ``load()`` symbols, labels of an external repository whose BUILD file
is in the tree, the module's own repository name, ``<placeholder>`` words in commands, and
``cmake -S <dir>``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dokimi.repos import parse_manifest
from dokimi.repos.l0 import scan_repo
from dokimi.repos.source import GitTree

FENCE = "```"
ALL = ["bazel", "make", "cmake", "pytest", "pre-commit"]


def _scan(tmp_path: Path, agents: str, files: dict[str, str], build_systems=ALL):
    root = tmp_path / "r"
    for rel, text in {"AGENTS.md": agents, "ev/tasks/t/task.json": "{}", **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")
    manifest = parse_manifest({
        "schema": "repo_manifest.v1", "id": "case", "source": {"url": "https://github.com/acme/case"},
        "eval_dir": "ev", "guidance": {"globs": ["**/AGENTS.md"]}, "entry_points": {"grade": "true"},
        "tasks": "ev/tasks", "build_systems": list(build_systems),
    })
    return scan_repo(GitTree(root / ".git", run("rev-parse", "HEAD")), manifest)


def _verdicts(payload) -> dict[tuple[str, str], str]:
    return {(c["kind"], c["claim"]): c["status"] for c in payload["claims"]}


def _fence(lang: str, *lines: str) -> str:
    return f"{FENCE}{lang}\n" + "\n".join(lines) + f"\n{FENCE}\n"


_DEFS = {"BUILD": "", "a/BUILD": 'cc_library(name = "core")\n',
         "defs.bzl": 'load(":other.bzl", "helper")\n\ndef m(name, srcs = []):\n    pass\n\n'
                     'm2 = rule(implementation = _impl)\n\ndef _private():\n    pass\n'}
_BUILD_SNIPPET = ('load("//:defs.bzl", "m")', "", "m(", '    name = "x",', "    srsc = [],",
                  '    deps = ["//a:core", "//a:kore"],', ")")
_EXTERNAL = {
    "BUILD": "", "MODULE.bazel": 'module(name = "case")\n',
    "third_party/repos.bzl": 'def deps():\n    zlib_repo(name = "zlib", build_file = "//third_party:zlib.BUILD")\n'
                             '    mkl_repo(name = "mkl", template = "mkl.tpl.BUILD", win = "mkl_win.tpl.BUILD")\n',
    "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n',
    "third_party/mkl.tpl.BUILD": 'cc_library(name = "mkl_core")\n',
    "third_party/mkl_win.tpl.BUILD": 'cc_library(name = "mkl_core_win")\n',
    "third_party/gen.BUILD": '[cc_library(name = n) for n in NAMES]\n',
    "third_party/stray.BUILD": 'cc_library(name = "stray")\n',  # named by no .bzl file: not known to be @stray's
}
_EXTERNAL["third_party/repos.bzl"] += '    gen_repo(name = "gen", build_file = "//third_party:gen.BUILD")\n'
_CMAKE = {"examples/app/CMakeLists.txt": "project(app)\n", "docs/conf.py": "", "sub/CMakeLists.txt": ""}

CASES = [
    # a BUILD snippet fenced as Python is checked as Starlark
    ("python fence with a load", _fence("python", *_BUILD_SNIPPET), _DEFS,
     {("starlark_macro", "m"): "ok", ("starlark_kwarg", "m(srsc=)"): "fail", ("starlark_kwarg", "m(deps=)"): "fail",
      ("starlark_load", "m from //:defs.bzl"): "ok", ("bazel_label", "//a:core"): "ok",
      ("bazel_label", "//a:kore"): "fail"}),
    ("python fence without a load: a macro called with name =",
     _fence("py", "m(", '    name = "x",', "    srsc = [],", ")"), _DEFS,
     {("starlark_macro", "m"): "ok", ("starlark_kwarg", "m(srsc=)"): "fail"}),
    ("python fence with a native rule only Bazel has",
     _fence("python3", "cc_library(", '    name = "x",', '    deps = ["//a:kore"],', ")"), _DEFS,
     {("bazel_label", "//a:kore"): "fail"}),
    ("python fence of plain Python", _fence("python", "import os", "m(1)", "run(", '    name = "x",', ")",
                                            'print("//a:kore")'), _DEFS, {}),
    ("python fence, bazel adapter off", _fence("python", *_BUILD_SNIPPET), _DEFS, {}),
    # load() symbols against the .bzl file in the tree
    ("load symbols", _fence("starlark", 'load("//:defs.bzl", "m", "gone", alias = "m2", "_private", "helper")',
                            'load(":rel.bzl", "x")', 'load("@other//:x.bzl", "y")'), _DEFS,
     {("starlark_load", "m from //:defs.bzl"): "ok", ("starlark_load", "gone from //:defs.bzl"): "fail",
      ("starlark_load", "m2 from //:defs.bzl"): "ok", ("starlark_load", "_private from //:defs.bzl"): "fail",
      ("starlark_load", "helper from //:defs.bzl"): "fail", ("starlark_load", "x from :rel.bzl"): None,
      ("starlark_load", "y from @other//:x.bzl"): None}),
    ("load from a .bzl the tree lacks: the label says so", _fence("starlark", 'load("//:gone.bzl", "m")'), _DEFS,
     {("starlark_load", "m from //:gone.bzl"): None, ("bazel_label", "//:gone.bzl"): "fail"}),
    # an external repository whose BUILD file is in the tree
    ("external repository BUILD file",
     "`@zlib//:zlib` `@zlib//:zlb` `@zlib//:zlib.h` `@zlib//sub:x` `@mkl//:mkl_core` `@mkl//:mkl_core_win` "
     "`@mkl//:mkl_cor` `@gen//:any` `@stray//:x` `@other//:x`", _EXTERNAL,
     {("bazel_label", "@zlib//:zlib"): "ok", ("bazel_label", "@zlib//:zlb"): "fail",
      ("bazel_label", "@zlib//:zlib.h"): "unresolved", ("bazel_label", "@zlib//sub:x"): "unresolved",
      ("bazel_label", "@mkl//:mkl_core"): "ok", ("bazel_label", "@mkl//:mkl_core_win"): "ok",
      ("bazel_label", "@mkl//:mkl_cor"): "fail", ("bazel_label", "@gen//:any"): "unresolved",
      ("bazel_label", "@stray//:x"): "unresolved", ("bazel_label", "@other//:x"): "unresolved"}),
    ("the module's own repository name alone", "Use full `@case//` labels; `@case//:nope` is checked here.",
     {"BUILD": "", "MODULE.bazel": 'module(name = "case")\n'},
     {("bazel_label", "@case//"): "ok", ("bazel_label", "@case//:nope"): "fail"}),
    # a <placeholder> in a label or a command is a word to fill in, not text to look up
    ("placeholder in a label", "`//pkg/<algo>:tests`\n"
     + _fence("bash", "bazel test --config=host //pkg/<algo>:tests", "make <target>", "pytest tests/<name>.py"),
     {"pkg/BUILD": "", ".bazelrc": "build:host --x\n", "Makefile": "all:\n", "tests/a.py": ""},
     {("bazel_config", "host"): "ok", ("bazel_label", "//pkg/"): None, ("bazel_label", "//pkg/$_:tests"): None,
      ("make_target", ">"): None, ("make_target", "$_"): None, ("pytest_path", "tests/"): None}),
    # cmake -S: the source directory has a CMakeLists.txt
    ("cmake -S after a cd", _fence("bash", "cd examples/app", "cmake -B build -S ."), _CMAKE,
     {("cmake_src", "."): "ok"}),
    ("cmake -S after a cd to a directory without CMakeLists.txt", _fence("bash", "cd docs", "cmake -B build -S ."),
     _CMAKE, {("cmake_src", "."): "fail"}),
    ("cmake -S with no cd: where it runs is not known", "`cmake -B build -S .`", _CMAKE,
     {("cmake_src", "."): "unresolved"}),
    ("cmake -S to a directory", _fence("bash", "cmake -S sub -B build", "cmake -Ssub -B build"), _CMAKE,
     {("cmake_src", "sub"): "ok"}),
    ("cmake -S after a cd that cannot be followed", _fence("bash", "cd $SRC && cmake -S . -B b", "cmake -S$X"),
     _CMAKE, {("cmake_src", "."): "unresolved", ("cmake_src", "$X"): None}),
]


@pytest.mark.parametrize("name, agents, files, expected", CASES, ids=[c[0] for c in CASES])
def test_rule(tmp_path, name, agents, files, expected):
    systems = ["make"] if name.endswith("bazel adapter off") else ALL
    got = _verdicts(_scan(tmp_path, agents + "\n", files, build_systems=systems))
    if not expected:
        assert got == {}, got
    assert {k: got.get(k) for k in expected} == expected, got


def test_starlark_load_claims_carry_the_load_line(tmp_path):
    agents = "intro\n\n" + _fence("python", *_BUILD_SNIPPET)
    claims = [c for c in _scan(tmp_path, agents, _DEFS)["claims"] if c["kind"] in ("starlark_load", "starlark_macro")]
    assert {(c["kind"], c["line"]) for c in claims} == {("starlark_load", 4), ("starlark_macro", 6)}
