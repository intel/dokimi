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
from dokimi.repos.l0.core import _FILL_IN
from dokimi.repos.source import GitTree

FENCE = "```"
ALL = ["bazel", "make", "cmake", "pytest", "pre-commit"]


def _scan(
    tmp_path: Path,
    agents: str,
    files: dict[str, str],
    build_systems=ALL,
    links: dict[str, str] | None = None,
    also_scan: list[str] | None = None,
):
    root = tmp_path / "r"
    for rel, text in {"AGENTS.md": agents, "ev/tasks/t/task.json": "{}", **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel, target in (links or {}).items():
        (root / rel).symlink_to(target)
    run = lambda *a: subprocess.run(
        ["git", "-C", str(root), *a],
        check=True,
        capture_output=True,  # noqa: E731
        text=True,
    ).stdout.strip()
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")
    manifest = parse_manifest(
        {
            "schema": "repo_manifest.v1",
            "id": "case",
            "source": {"url": "https://github.com/acme/case"},
            "eval_dir": "ev",
            "guidance": {"globs": ["**/AGENTS.md"], **({"also_scan": also_scan} if also_scan else {})},
            "entry_points": {"grade": "true"},
            "tasks": "ev/tasks",
            "build_systems": list(build_systems),
        }
    )
    return scan_repo(GitTree(root / ".git", run("rev-parse", "HEAD")), manifest)


def _verdicts(payload) -> dict[tuple[str, str], str]:
    return {(c["kind"], c["claim"]): c["status"] for c in payload["claims"]}


def _fence(lang: str, *lines: str) -> str:
    return f"{FENCE}{lang}\n" + "\n".join(lines) + f"\n{FENCE}\n"


_DEFS = {
    "BUILD": "",
    "a/BUILD": 'cc_library(name = "core")\n',
    "defs.bzl": 'load(":other.bzl", "helper")\n\ndef m(name, srcs = []):\n    pass\n\n'
    "m2 = rule(implementation = _impl)\n\ndef _private():\n    pass\n",
}
_BUILD_SNIPPET = (
    'load("//:defs.bzl", "m")',
    "",
    "m(",
    '    name = "x",',
    "    srsc = [],",
    '    deps = ["//a:core", "//a:kore"],',
    ")",
)
_EXTERNAL = {
    "BUILD": "",
    "MODULE.bazel": 'module(name = "case")\n',
    "third_party/repos.bzl": 'def deps():\n    zlib_repo(name = "zlib", build_file = "//third_party:zlib.BUILD")\n'
    '    mkl_repo(name = "mkl", template = "mkl.tpl.BUILD", win = "mkl_win.tpl.BUILD")\n',
    "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n',
    "third_party/mkl.tpl.BUILD": 'cc_library(name = "mkl_core")\n',
    "third_party/mkl_win.tpl.BUILD": 'cc_library(name = "mkl_core_win")\n',
    "third_party/gen.BUILD": "[cc_library(name = n) for n in NAMES]\n",
    "third_party/stray.BUILD": 'cc_library(name = "stray")\n',  # named by no .bzl file: not known to be @stray's
}
_EXTERNAL["third_party/repos.bzl"] += (
    '    gen_repo(name = "gen", build_file = "//third_party:gen.BUILD")\n'
)
_EXTERNAL_REVIEW = {
    "BUILD": "",
    "MODULE.bazel": 'module(name = "case")\n',
    "third_party/repos.bzl": 'def deps():\n    zlib_repo(name = "zlib", build_file = "//third_party:zlib.BUILD")\n'
    '    lib_repo(name = "lib", build_file = "//third_party:lib.BUILD")\n'
    '    tools_repo(name = "lib_tools", build_file = "//third_party:lib_tools.BUILD")\n'
    '    # old_repo(name = "zlib", build_file = "//old:zlib.BUILD")\n',
    "third_party/zlib.BUILD": 'exports_files(["LICENSE"])\ncc_library(name = "zlib", srcs = ["adler32"])\n',
    "old/zlib.BUILD": 'cc_library(name = "stale")\n',  # same basename, referenced only in a comment
    "third_party/lib.BUILD": 'cc_library(name = "lib")\n',
    "third_party/lib_tools.BUILD": 'cc_library(name = "tool")\n',
}
_BZL_UNPACK = {
    "BUILD": "",
    "defs.bzl": "MyInfo, _new_myinfo = provider(init = _init)\n",
    "pair.bzl": "(A, B) = (1, 2)\n",
}
_CMAKE_LINKS = {"src/CMakeLists.txt": "", ".gitmodules": '[submodule "lib"]\n\tpath = vendor/lib\n'}
_CMAKE = {
    "examples/app/CMakeLists.txt": "project(app)\n",
    "docs/conf.py": "",
    "sub/CMakeLists.txt": "",
}

CASES = [
    # a BUILD snippet fenced as Python is checked as Starlark
    (
        "python fence with a load",
        _fence("python", *_BUILD_SNIPPET),
        _DEFS,
        {
            ("starlark_macro", "m"): "ok",
            ("starlark_kwarg", "m(srsc=)"): "fail",
            ("starlark_kwarg", "m(deps=)"): "fail",
            ("starlark_load", "m from //:defs.bzl"): "ok",
            ("bazel_label", "//a:core"): "ok",
            ("bazel_label", "//a:kore"): "fail",
        },
    ),
    (
        "python fence of a macro call alone is plain Python",
        _fence("py", "m(", '    name = "x",', "    srsc = [],", ")"),
        _DEFS,
        {},
    ),
    (
        "python fence with test_suite and strings that look like labels",
        _fence(
            "python", "test_suite(", '    name = "x",', ")", 'p = x.split("//")', 'urls = ["//a/b"]'
        ),
        _DEFS,
        {},
    ),
    (
        "python fence calling run( when a .bzl defines run",
        _fence("python", "run(", '    name = "job",', "    verbose = True,", ")"),
        {**_DEFS, "run.bzl": 'def run(name, mode = "x"):\n    pass\n'},
        {},
    ),
    (
        "starlark fence: a URL string is not a label",
        _fence("starlark", 'url = "//cdn.example.com/x.tar"'),
        _DEFS,
        {},
    ),
    (
        "python fence loading a relative label",
        _fence("python", 'load(":x.bzl", "m")', 'y = ["//a:kore"]'),
        _DEFS,
        {("bazel_label", "//a:kore"): "fail"},
    ),
    (
        "python fence loading from a repository",
        _fence("python", 'load("@rules//:x.bzl", "m")', 'y = ["//a:kore"]'),
        _DEFS,
        {("bazel_label", "//a:kore"): "fail"},
    ),
    (
        "python fence with a load over several lines",
        _fence("python", "load(", '    "//:defs.bzl",', '    "m",', ")", 'y = ["//a:kore"]'),
        _DEFS,
        {("bazel_label", "//a:kore"): "fail", ("starlark_load", "m from //:defs.bzl"): "ok"},
    ),
    (
        "a single-quoted load of a file the tree lacks, in a python fence",
        _fence("python", "load('//:gone.bzl', 'm')"),
        _DEFS,
        {("bazel_label", "//:gone.bzl"): "fail"},
    ),
    (
        "a single-quoted load of a file the tree lacks, in a starlark fence",
        _fence("starlark", "load('//:gone.bzl', 'm')"),
        _DEFS,
        {("bazel_label", "//:gone.bzl"): "fail"},
    ),
    (
        "a .bzl whose string ends in an escaped backslash still defines what follows",
        _fence("starlark", 'load("//:defs.bzl", "m", "n")'),
        {
            "BUILD": "",
            "defs.bzl": 'SEP = "\\\\"\ndef m(name):\n    pass\n\ndef n(name):\n    pass\n',
        },
        {
            ("starlark_load", "m from //:defs.bzl"): "ok",
            ("starlark_load", "n from //:defs.bzl"): "ok",
        },
    ),
    (
        "python fence with a native rule only Bazel has",
        _fence("python3", "cc_library(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        _DEFS,
        {("bazel_label", "//a:kore"): "fail"},
    ),
    (
        "python fence of plain Python",
        _fence("python", "import os", "m(1)", "run(", '    name = "x",', ")", 'print("//a:kore")'),
        _DEFS,
        {},
    ),
    ("python fence, bazel adapter off", _fence("python", *_BUILD_SNIPPET), _DEFS, {}),
    # load() symbols against the .bzl file in the tree
    (
        "load symbols",
        _fence(
            "starlark",
            'load("//:defs.bzl", "m", "gone", alias = "m2", "_private", "helper")',
            'load(":rel.bzl", "x")',
            'load("@other//:x.bzl", "y")',
        ),
        _DEFS,
        {
            ("starlark_load", "m from //:defs.bzl"): "ok",
            ("starlark_load", "gone from //:defs.bzl"): "fail",
            ("starlark_load", "m2 from //:defs.bzl"): "ok",
            ("starlark_load", "_private from //:defs.bzl"): "fail",
            ("starlark_load", "helper from //:defs.bzl"): "fail",
            ("starlark_load", "x from :rel.bzl"): None,
            ("starlark_load", "y from @other//:x.bzl"): None,
        },
    ),
    (
        "load from a .bzl the tree lacks: the label says so",
        _fence("starlark", 'load("//:gone.bzl", "m")'),
        _DEFS,
        {("starlark_load", "m from //:gone.bzl"): None, ("bazel_label", "//:gone.bzl"): "fail"},
    ),
    # an external repository whose BUILD file is in the tree
    (
        "external repository BUILD file",
        "`@zlib//:zlib` `@zlib//:zlb` `@zlib//:zlib.h` `@zlib//sub:x` `@mkl//:mkl_core` `@mkl//:mkl_core_win` "
        "`@mkl//:mkl_cor` `@gen//:any` `@stray//:x` `@other//:x`",
        _EXTERNAL,
        {
            ("bazel_label", "@zlib//:zlib"): "ok",
            ("bazel_label", "@zlib//:zlb"): "fail",
            ("bazel_label", "@zlib//:zlib.h"): "unresolved",
            ("bazel_label", "@zlib//sub:x"): "unresolved",
            ("bazel_label", "@mkl//:mkl_core"): "ok",
            ("bazel_label", "@mkl//:mkl_core_win"): "ok",
            ("bazel_label", "@mkl//:mkl_cor"): "fail",
            ("bazel_label", "@gen//:any"): "unresolved",
            ("bazel_label", "@stray//:x"): "unresolved",
            ("bazel_label", "@other//:x"): "unresolved",
        },
    ),
    (
        "external repository: wildcards, exported and source files, which BUILD file is the repository's",
        "`@zlib//:all` `@zlib//:all-targets` `@zlib//:LICENSE` `@zlib//:adler32` `@zlib//:stale` `@lib//:tool` "
        "`@lib//:lib`",
        _EXTERNAL_REVIEW,
        {
            ("bazel_label", "@zlib//:all"): "ok",
            ("bazel_label", "@zlib//:all-targets"): "ok",
            ("bazel_label", "@zlib//:LICENSE"): "ok",
            ("bazel_label", "@zlib//:adler32"): "unresolved",
            ("bazel_label", "@zlib//:stale"): "fail",
            ("bazel_label", "@lib//:tool"): "fail",
            ("bazel_label", "@lib//:lib"): "ok",
        },
    ),
    (
        "load of a name an unpacking assignment binds",
        _fence(
            "starlark",
            'load("//:defs.bzl", "MyInfo", "_new_myinfo", "Gone")',
            'load("//:pair.bzl", "A")',
        ),
        _BZL_UNPACK,
        {
            ("starlark_load", "MyInfo from //:defs.bzl"): "ok",
            ("starlark_load", "_new_myinfo from //:defs.bzl"): "fail",
            ("starlark_load", "Gone from //:defs.bzl"): "fail",
            ("starlark_load", "A from //:pair.bzl"): "unresolved",
        },
    ),
    (
        "the module's own repository name alone",
        "Use full `@case//` labels; `@case//:nope` is checked here.",
        {"BUILD": "", "MODULE.bazel": 'module(name = "case")\n'},
        {("bazel_label", "@case//"): "ok", ("bazel_label", "@case//:nope"): "fail"},
    ),
    # a <placeholder> in a label or a command is a word to fill in, not text to look up
    (
        "placeholder in a label",
        "`//pkg/<algo>:tests`\n"
        + _fence(
            "bash",
            "bazel test --config=host //pkg/<algo>:tests",
            "make <target>",
            "pytest tests/<name>.py",
        ),
        {"pkg/BUILD": "", ".bazelrc": "build:host --x\n", "Makefile": "all:\n", "tests/a.py": ""},
        {
            ("bazel_config", "host"): "ok",
            ("bazel_label", "//pkg/"): None,
            ("bazel_label", "//pkg/$_:tests"): None,
            ("make_target", ">"): None,
            ("make_target", "$_"): None,
            ("pytest_path", "tests/"): None,
        },
    ),
    # cmake -S: the source directory has a CMakeLists.txt
    (
        "cmake -S after a cd",
        _fence("bash", "cd examples/app", "cmake -B build -S ."),
        _CMAKE,
        {("cmake_src", "."): "ok"},
    ),
    (
        "cmake -S after a cd to a directory without CMakeLists.txt",
        _fence("bash", "cd docs", "cmake -B build -S ."),
        _CMAKE,
        {("cmake_src", "."): "fail"},
    ),
    (
        "cmake -S with no cd: where it runs is not known",
        "`cmake -B build -S .`",
        _CMAKE,
        {("cmake_src", "."): "unresolved"},
    ),
    (
        "cmake -S to a directory",
        _fence("bash", "cmake -S sub -B build", "cmake -Ssub -B build"),
        _CMAKE,
        {("cmake_src", "sub"): "ok"},
    ),
    (
        "cmake -S in a submodule after a cd",
        _fence("bash", "cd . && cmake -S vendor/lib -B build"),
        _CMAKE_LINKS,
        {("cmake_src", "vendor/lib"): "unresolved"},
    ),
    (
        "cmake -S with a glob is not a literal directory",
        _fence("bash", "cd . && cmake -S examples/* -B build"),
        _CMAKE,
        {("cmake_src", "examples/*"): None},
    ),
    (
        "cmake -S after a cd that cannot be followed",
        _fence("bash", "cd $SRC && cmake -S . -B b", "cmake -S$X"),
        _CMAKE,
        {("cmake_src", "."): "unresolved", ("cmake_src", "$X"): None},
    ),
]


def _external(files: dict[str, str]) -> dict[str, str]:
    return {"BUILD": "", **files}


# One repository per BUILD file name form and per way of naming it: the whole claim set is the verdict
_BUILD_FORMS = [
    (
        "BUILD.zlib.bazel",
        {
            "third_party/repos.bzl": 'r(name = "zlib", build_file = "//third_party:BUILD.zlib.bazel")\n',
            "third_party/BUILD.zlib.bazel": 'cc_library(name = "t")\n',
        },
    ),
    (
        "zlib.BUILD.bazel",
        {
            "third_party/repos.bzl": 'r(name = "zlib", build_file = "//third_party:zlib.BUILD.bazel")\n',
            "third_party/zlib.BUILD.bazel": 'cc_library(name = "t")\n',
        },
    ),
    (
        "a root-relative path",
        {
            "third_party/repos.bzl": 'r(name = "zlib", build_file = "third_party/zlib.BUILD")\n',
            "third_party/zlib.BUILD": 'cc_library(name = "t")\n',
        },
    ),
    (
        "a label relative to the .bzl file",
        {
            "third_party/repos.bzl": 'r(name = "zlib", build_file = ":zlib.BUILD")\n',
            "third_party/zlib.BUILD": 'cc_library(name = "t")\n',
        },
    ),
    (
        "WORKSPACE.bazel",
        {
            "WORKSPACE.bazel": 'r(name = "zlib", build_file = "//third_party:zlib.BUILD")\n',
            "third_party/zlib.BUILD": 'cc_library(name = "t")\n',
        },
    ),
    (
        "MODULE.bazel",
        {
            "MODULE.bazel": 'module(name = "case")\nr(name = "zlib", build_file = "//third_party:zlib.BUILD")\n',
            "third_party/zlib.BUILD": 'cc_library(name = "t")\n',
        },
    ),
]


@pytest.mark.parametrize("files", [f for _, f in _BUILD_FORMS], ids=[n for n, _ in _BUILD_FORMS])
def test_external_build_file_name_forms(tmp_path, files):
    got = _verdicts(_scan(tmp_path, "`@zlib//:t` `@zlib//:nope`\n", _external(files)))
    assert got == {("bazel_label", "@zlib//:t"): "ok", ("bazel_label", "@zlib//:nope"): "fail"}


def test_a_longer_repository_name_does_not_claim_the_shorter_ones_build_file(tmp_path):
    files = _external(
        {
            "third_party/repos.bzl": 'r(name = "lib", build_file = "//third_party:lib.BUILD")\n'
            'r(name = "lib_tools", build_file = "//third_party:lib_tools_win.BUILD")\n',
            "third_party/lib.BUILD": 'cc_library(name = "lib")\n',
            "third_party/lib_tools_win.BUILD": 'cc_library(name = "win")\n',
        }
    )
    got = _verdicts(_scan(tmp_path, "`@lib//:win` `@lib//:lib`\n", files))
    assert got == {("bazel_label", "@lib//:win"): "fail", ("bazel_label", "@lib//:lib"): "ok"}


@pytest.mark.parametrize(
    "ref",
    [
        {"WORKSPACE": 'http_archive(name = "zlib", build_file = "//third_party:zlib.BUILD")\n'},
        {"third_party/repos.bzl": 'r(name = "zlib", build_file = "//third_party:zlib.BUILD")\n'},
    ],
    ids=["WORKSPACE", "bzl"],
)
@pytest.mark.parametrize(
    "dep, expected",
    [
        (
            'bazel_dep(name = "zlib", version = "1.3.1")',
            "unresolved",
        ),  # the registry module, not the leftover BUILD
        (
            'bazel_dep(name = "zlib", version = "1.3.1", repo_name = "z2")',
            "fail",
        ),  # @zlib is not that module
        ('# bazel_dep(name = "zlib", version = "1.3.1")', "fail"),
    ],
)
def test_a_bazel_dep_is_not_checked_against_a_leftover_build_file(tmp_path, ref, dep, expected):
    files = _external(
        {
            "MODULE.bazel": f'module(name = "me")\n{dep}\n',
            "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n',
            **ref,
        }
    )
    got = _verdicts(_scan(tmp_path, "`@zlib//:z`\n", files))
    assert got == {("bazel_label", "@zlib//:z"): expected}


@pytest.mark.parametrize("lang", ["python", "starlark"])
def test_a_fence_nested_in_a_list_item_is_checked_as_at_the_top_level(tmp_path, lang):
    fence = _fence(lang, *_BUILD_SNIPPET)
    nested = "".join(f"   {line}\n" if line else "\n" for line in fence.splitlines())
    claims = lambda p, shift=0: {
        (c["line"] - shift, c["kind"], c["claim"], c["status"])  # noqa: E731
        for c in p["claims"]
    }
    top = claims(_scan(tmp_path / "top", fence, _DEFS))
    assert {k for _, k, _, _ in top} >= {
        "starlark_load",
        "starlark_macro",
        "starlark_kwarg",
        "bazel_label",
    }
    assert (
        claims(_scan(tmp_path / "nested", "1. Add this to BUILD:\n\n" + nested, _DEFS), shift=2)
        == top
    )


def test_a_computed_first_build_file_is_not_forgotten_by_the_second(tmp_path):
    files = _external(
        {
            "third_party/repos.bzl": 'r(name = "lib", build_file = "//third_party:lib.BUILD")\n'
            'r(name = "lib", build_file = "//third_party:lib_win.BUILD")\n',
            "third_party/lib.BUILD": "[cc_library(name = n) for n in NAMES]\n",
            "third_party/lib_win.BUILD": 'cc_library(name = "win")\n',
        }
    )
    got = _verdicts(_scan(tmp_path, "`@lib//:zz` `@lib//:win`\n", files))
    assert got == {("bazel_label", "@lib//:zz"): "unresolved", ("bazel_label", "@lib//:win"): "ok"}


@pytest.mark.parametrize(
    "build, label, expected",
    [
        ('exports_files(glob(["*"]))\ncc_library(name = "zlib")\n', "LICENSE", "unresolved"),
        ('exports_files(["LICENSE", "NOTICE"])\ncc_library(name = "zlib")\n', "NOTICE", "ok"),
        ('exports_files(["LICENSE", "NOTICE"])\ncc_library(name = "zlib")\n', "OTHER", "fail"),
        ('exports_files(srcs = ["LICENSE", "NOTICE"])\n', "NOTICE", "ok"),
        ('exports_files(FILES)\ncc_library(name = "zlib")\n', "NOTICE", "unresolved"),
        (
            'cc_library(name = "%{target_name}")\n',
            "zlib",
            "unresolved",
        ),  # a template, rendered on fetching
        (
            'cc_library(name = "a")\n%{extra_targets}\n',
            "b",
            "unresolved",
        ),  # a bare placeholder injects targets
        (
            'cc_library(name = "a", srcs = ["%{repo_root}/lib/a.a"])\n',
            "kore",
            "fail",
        ),  # a path makes no target
    ],
)
def test_external_exports_files_and_templates(tmp_path, build, label, expected):
    files = _external(
        {
            "third_party/repos.bzl": 'r(name = "zlib", build_file = "//third_party:zlib.tpl.BUILD")\n',
            "third_party/zlib.tpl.BUILD": build,
        }
    )
    got = _verdicts(_scan(tmp_path, f"`@zlib//:{label}`\n", files))
    assert got == {("bazel_label", f"@zlib//:{label}"): expected}


@pytest.mark.parametrize("name, agents, files, expected", CASES, ids=[c[0] for c in CASES])
def test_rule(tmp_path, name, agents, files, expected):
    systems = ["make"] if name.endswith("bazel adapter off") else ALL
    got = _verdicts(_scan(tmp_path, agents + "\n", files, build_systems=systems))
    if not expected:
        assert got == {}, got
    assert {k: got.get(k) for k in expected} == expected, got


def test_cmake_source_through_a_directory_link(tmp_path):
    agents = _fence("bash", "cd current", "cmake -S . -B build")
    got = _verdicts(
        _scan(tmp_path, agents + "\n", {"src/CMakeLists.txt": ""}, links={"current": "src"})
    )
    assert got[("cmake_src", ".")] == "ok", got


def test_starlark_load_claims_carry_the_load_line(tmp_path):
    agents = "intro\n\n" + _fence("python", *_BUILD_SNIPPET)
    claims = [
        c
        for c in _scan(tmp_path, agents, _DEFS)["claims"]
        if c["kind"] in ("starlark_load", "starlark_macro")
    ]
    assert {(c["kind"], c["line"]) for c in claims} == {("starlark_load", 4), ("starlark_macro", 6)}


def test_unreadable_bzl_or_external_build_is_unresolved_with_the_reason(tmp_path):
    agents = _fence("starlark", 'load("//:defs.bzl", "m")') + "\n`@zlib//:zlib`\n"
    files = {
        **_EXTERNAL,
        "defs.bzl": "def m(name):\n    pass\n\0",  # a NUL byte: read as binary, so not read
        "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n\0',
    }
    claims = {(c["kind"], c["claim"]): c for c in _scan(tmp_path, agents, files)["claims"]}
    load = claims[("starlark_load", "m from //:defs.bzl")]
    assert load["status"] == "unresolved" and "not read" in load["why"], load
    label = claims[("bazel_label", "@zlib//:zlib")]
    assert label["status"] == "unresolved" and "not read" in label["why"], label


def test_unreadable_build_inputs_warn_and_mark_the_scan_incomplete(tmp_path):
    agents = _fence("starlark", 'load("//:defs.bzl", "m")') + "\n`@zlib//:zlib`\n"
    files = {
        **_EXTERNAL,
        "defs.bzl": "def m(name):\n    pass\n\0",
        "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n\0',
    }
    payload = _scan(tmp_path, agents, files)
    assert payload["incomplete"] is True
    assert any(w.startswith("build file not scanned: defs.bzl: ") and "binary" in w for w in payload["warnings"])
    assert any(w.startswith("build file not scanned: third_party/zlib.BUILD: ") for w in payload["warnings"])


@pytest.mark.parametrize("path", ["a/BUILD", "WORKSPACE", "MODULE.bazel", "tools/x.bazelrc", "BUILD.bazel", "WORKSPACE.bazel"])
def test_each_bazel_input_name_warns_when_unreadable(tmp_path, path):
    payload = _scan(tmp_path, _fence("bash", "bazel build //a:b") + "\n`SOME_IDENT`\n", {path: "x\0"})
    assert [w for w in payload["warnings"] if w.startswith("build file not scanned: ")] == [
        f"build file not scanned: {path}: binary (a NUL byte)"
    ]
    assert payload["incomplete"] is True


@pytest.mark.parametrize("path", ["BUILD_status.png", "WORKSPACE_logo.png", "BUILDING.md.bin", "a/MODULE.bazel.orig"])
def test_a_file_that_only_starts_like_a_bazel_input_does_not_warn(tmp_path, path):
    payload = _scan(tmp_path, "`SOME_IDENT`\n", {path: "x\0", "Makefile": "all:\n"})
    assert not [w for w in payload["warnings"] if w.startswith("build file not scanned: ")]
    assert payload["incomplete"] is False


def test_an_unreadable_bazel_named_file_is_ignored_when_bazel_is_not_a_build_system(tmp_path):
    payload = _scan(tmp_path, "`SOME_IDENT`\n", {"BUILD": "x\0", "a.bzl": "y\0"}, build_systems=["make"])
    assert not [w for w in payload["warnings"] if w.startswith("build file not scanned: ")]
    assert payload["incomplete"] is False


def test_a_guidance_file_that_is_also_a_build_input_warns_once(tmp_path):
    payload = _scan(tmp_path, "see `x/BUILD`\n", {"x/BUILD": "y\0"}, also_scan=["x/BUILD"])
    unread = [w for w in payload["warnings"] if "x/BUILD" in w and "not scanned" in w]
    assert len(unread) == 1, unread
    assert payload["incomplete"] is True


def test_an_unread_bazelrc_import_of_any_name_marks_the_scan_incomplete(tmp_path):
    agents = _fence("bash", "bazel build --config=ci //:x") + "\n"
    files = {"BUILD": "", ".bazelrc": "import %workspace%/tools/ci.rc\n", "tools/ci.rc": "build:ci --x\n\0"}
    payload = _scan(tmp_path, agents, files)
    assert _verdicts(payload)[("bazel_config", "ci")] == "unresolved"
    assert payload["incomplete"] is True
    assert [w for w in payload["warnings"] if w.startswith("build file not scanned: ")] == [
        "build file not scanned: tools/ci.rc: binary (a NUL byte)"
    ]


@pytest.mark.parametrize(
    "cmd",
    [
        "cd nocm && cmake -S <path/to/src> -B b",
        "cd nocm && cmake -S <source dir> -B b",
        "cd nocm && cmake -S C:/x/y -B b",
        "cd nocm && cmake -S %SRC% -B b",
        "cd nocm && cmake -S /abs/path -B b",
        "cd nocm && cmake -S ~/src -B b",
        "cd nocm && cmake -S -Wno-dev -B b",
        "cd nocm && cmake -S examples/?pp -B b",
        "cd nocm && cmake -S examples/[ab]pp -B b",
        "cd nocm && cmake -S examples/{a,b} -B b",
        "cd nocm && cmake -S src\\app -B b",  # unquoted: shlex drops the backslash, so the raw text is checked
        "cd nocm && cmake -S ..\\src -B b",
    ],
)
def test_cmake_source_that_is_not_a_literal_tree_path_is_not_claimed(tmp_path, cmd):
    got = _verdicts(_scan(tmp_path, _fence("bash", cmd) + "\n", {"nocm/x.txt": "", **_CMAKE}))
    assert got == {}, got


def test_redirections_next_to_a_placeholder_are_not_placeholders(tmp_path):
    agents = _fence("bash", "cd nocm && cmake -S examples/app -B b < in.txt >out.txt")
    got = _verdicts(_scan(tmp_path, agents + "\n", {"nocm/x.txt": "", **_CMAKE}))
    assert got == {("cmake_src", "examples/app"): "fail"}, got


def test_a_script_path_after_an_input_redirection_is_still_checked(tmp_path):
    # the redirected command gives the same claims as the plain one: the path is not swallowed into a placeholder
    plain = _verdicts(
        _scan(
            tmp_path / "a", _fence("bash", "python scripts/missing.py") + "\n", {"nocm/x.txt": ""}
        )
    )
    redirected = _verdicts(
        _scan(
            tmp_path / "b",
            _fence("bash", "python <in scripts/missing.py 2>err.log") + "\n",
            {"nocm/x.txt": ""},
        )
    )
    assert ("script_path", "scripts/missing.py") in plain and redirected == plain, (
        plain,
        redirected,
    )


def test_unread_module_bazel_leaves_an_external_label_unresolved_with_the_reason(tmp_path):
    # MODULE.bazel may declare @zlib as a bazel_dep, which the WORKSPACE-era BUILD file would not be
    files = {
        "MODULE.bazel": 'module(name = "case")\nbazel_dep(name = "zlib", version = "1")\n\0',
        "WORKSPACE": 'zlib_repo(name = "zlib", build_file = "//third_party:zlib.BUILD")\n',
        "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n',
    }
    claims = {(c["kind"], c["claim"]): c for c in _scan(tmp_path, "`@zlib//:z`\n", files)["claims"]}
    label = claims[("bazel_label", "@zlib//:z")]
    assert label["status"] == "unresolved" and "MODULE.bazel was not read" in label["why"], label


def test_unread_local_build_file_leaves_a_label_unresolved_with_the_reason(tmp_path):
    claims = {
        (c["kind"], c["claim"]): c
        for c in _scan(tmp_path, "`//a:kore`\n", {"a/BUILD": 'cc_library(name = "core")\n\0'})[
            "claims"
        ]
    }
    label = claims[("bazel_label", "//a:kore")]
    assert label["status"] == "unresolved" and "a/BUILD was not read" in label["why"], label


def test_an_unreadable_external_build_file_is_read_once_for_all_its_claims(tmp_path, monkeypatch):
    reads: list[str] = []
    real = GitTree.read_many

    def spy(self, paths, *args, **kwargs):
        reads.extend(paths)
        return real(self, paths, *args, **kwargs)

    monkeypatch.setattr(GitTree, "read_many", spy)
    agents = "`@zlib//:t1` `@zlib//:t2` `@zlib//:t3` `@zlib//:t4` `@zlib//:t5`\n"
    files = {
        "WORKSPACE": 'zlib_repo(name = "zlib", build_file = "//third_party:zlib.BUILD")\n',
        "third_party/zlib.BUILD": 'cc_library(name = "zlib")\n\0',
    }
    _scan(tmp_path, agents, files)
    assert reads.count("third_party/zlib.BUILD") == 1, reads


def test_every_string_form_of_a_label_is_checked_on_its_own_line(tmp_path):
    agents = _fence("starlark", "x = 1", 'y = r"//a:kore"', "z = '''//a:kore'''", "w = b'//a:kore'")
    claims = [
        c for c in _scan(tmp_path, agents + "\n", _DEFS)["claims"] if c["kind"] == "bazel_label"
    ]
    assert sorted((c["status"], c["line"]) for c in claims) == [
        ("fail", 3),
        ("fail", 4),
        ("fail", 5),
    ]


@pytest.mark.parametrize(
    "call",
    [
        "glob",
        "select",
        "alias",
        "module",
        "platform",
        "toolchain",
        "workspace",
        "licenses",
        "exports_files",
        "package_group",
        "package",
        "test_suite",
    ],
)
def test_python_fence_calling_a_name_plain_python_also_uses_is_plain_python(tmp_path, call):
    agents = _fence("python", f"{call}(", '    name = "x",', ")", 'print("//a:kore")')
    assert _verdicts(_scan(tmp_path, agents, _DEFS)) == {}


@pytest.mark.parametrize(
    "lines",
    [
        ("http_archive(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        ("genrule(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        ("py_test(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        ("config_setting(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        ("# BUILD", "cc_library(", '    name = "x",', '    deps = ["//a:kore"],', ")"),
        (
            "def build():",
            "    cc_library(",
            '        name = "x",',
            '        deps = ["//a:kore"],',
            "    )",
        ),
    ],
)
def test_python_fence_calling_a_bazel_rule_reads_as_bazel(tmp_path, lines):
    agents = _fence("python", *lines)
    assert _verdicts(_scan(tmp_path, agents, _DEFS)) == {("bazel_label", "//a:kore"): "fail"}


@pytest.mark.parametrize(
    "cmd",
    [
        "cd nocm && cmake -Sexamples\\app -B b",  # shlex joins the -S value, so the raw token is checked
    ],
)
def test_cmake_source_with_a_backslash_in_the_same_word_is_not_claimed(tmp_path, cmd):
    got = _verdicts(_scan(tmp_path, _fence("bash", cmd) + "\n", {"nocm/x.txt": "", **_CMAKE}))
    assert got == {}, got


@pytest.mark.parametrize(
    "cmd, want",
    [
        # a backslash in another word of the same command does not drop the -S claim
        (
            "cd nocm && cmake -S missing -B b && cmake -S a\\b -B c",
            {("cmake_src", "missing"): "fail"},
        ),
        ("cd nocm && cmake -S missing -B b --toolchain x-S\\y", {("cmake_src", "missing"): "fail"}),
        (
            "cd nocm && cmake -S missing -B b -DP=x-Sy\\z",
            {("cmake_src", "missing"): "fail", ("cmake_var", "P"): "fail"},
        ),
        ("cd nocm && cmake -S missingdir -B b\\x", {("cmake_src", "missingdir"): "fail"}),
    ],
)
def test_a_backslash_in_another_word_does_not_drop_a_cmake_source_claim(tmp_path, cmd, want):
    got = _verdicts(_scan(tmp_path, _fence("bash", cmd) + "\n", {"nocm/x.txt": "", **_CMAKE}))
    assert got == want, got


@pytest.mark.parametrize(
    "cmd",
    [
        "python <in.txt scripts/missing.py>out",
        "python <in scripts/missing.py>&2",
        "make <x-y> && python scripts/missing.py",
        "python <in scripts/missing.py >b",
        "python <in.txt scripts/missing.py 2>&1",
    ],
)
def test_placeholder_and_redirection_clauses_keep_the_claims_of_the_plain_command(tmp_path, cmd):
    plain = _verdicts(
        _scan(
            tmp_path / "a", _fence("bash", "python scripts/missing.py") + "\n", {"nocm/x.txt": ""}
        )
    )
    got = _verdicts(_scan(tmp_path / "b", _fence("bash", cmd) + "\n", {"nocm/x.txt": ""}))
    assert ("script_path", "scripts/missing.py") in plain and got == plain, (plain, got)


def test_a_heredoc_followed_by_a_redirection_is_not_a_placeholder():
    # the second `<` of `<<EOF>` is part of the heredoc operator, not the start of a placeholder
    assert _FILL_IN.search("cat <<EOF>out") is None
    assert _FILL_IN.search("python <in.txt scripts/x.py>out") is None
    assert _FILL_IN.search("cat <path/to/src> > log") is not None


def test_unread_module_bazel_without_a_build_file_is_unresolved_with_the_reason(tmp_path):
    # no BUILD file names @other: the unread MODULE.bazel may declare it, so the label is unresolved, not failed
    files = {"MODULE.bazel": 'module(name = "case")\n\0', "WORKSPACE": ""}
    claims = {
        (c["kind"], c["claim"]): c for c in _scan(tmp_path, "`@other//:z`\n", files)["claims"]
    }
    label = claims[("bazel_label", "@other//:z")]
    assert (
        label["status"] == "unresolved"
        and label["why"].startswith("MODULE.bazel was not read (")
        and "NUL byte" in label["why"]
    ), label


def test_unread_bazelrc_leaves_a_config_unresolved_not_missing(tmp_path):
    # a .bazelrc that cannot be read may hold `build:ci`: the config is unresolved with the reason, not a failure
    files = {".bazelrc": "build:dev --copt=-O0\n\0"}
    claims = {
        (c["kind"], c["claim"]): c
        for c in _scan(tmp_path, _fence("bash", "bazel build --config=ci //x") + "\n", files)[
            "claims"
        ]
    }
    config = claims[("bazel_config", "ci")]
    assert config["status"] == "unresolved" and config["why"].startswith(
        ".bazelrc was not read ("
    ), config
