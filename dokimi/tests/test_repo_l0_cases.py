# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One small repository per L0 rule: the verdict each claim gets, and the CLI's exit codes.

Each case is a set of files and one guidance line. The cases come from review:
every rule here was once wrong in a way the end-to-end fixture did not notice.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos import parse_manifest
from dokimi.repos.l0 import scan_repo
from dokimi.repos.source import GitTree, WorkTree

FENCE = "```"
U = "https://github.com/acme/case"
ALL = ["bazel", "make", "cmake", "pytest", "pre-commit"]


def _manifest(build_systems=ALL, also_scan=()):
    return parse_manifest({
        "schema": "repo_manifest.v1", "id": "case", "source": {"url": "https://github.com/acme/case"},
        "eval_dir": "ev", "guidance": {"globs": ["**/AGENTS.md"], "also_scan": list(also_scan)},
        "entry_points": {"grade": "true"}, "tasks": "ev/tasks", "build_systems": list(build_systems),
    })


def _git_repo(root: Path, files: dict[str, str]) -> GitTree:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")
    return GitTree(root / ".git", run("rev-parse", "HEAD"))


def _scan(tmp_path, agents: str, files: dict[str, str] | None = None, **manifest):
    tree = _git_repo(tmp_path / "r", {"AGENTS.md": agents, "ev/tasks/t/task.json": "{}", **(files or {})})
    return scan_repo(tree, _manifest(**manifest))


def _verdicts(payload) -> dict[tuple[str, str], str]:
    return {(c["kind"], c["claim"]): c["status"] for c in payload["claims"]}


def _shell(*lines: str) -> str:
    return f"{FENCE}bash\n" + "\n".join(lines) + f"\n{FENCE}\n"


CASES = [
    # paths
    ("parent dir exists: a rename", "`sub/gone.h`", {"sub/x.h": "", "other/sub/gone.h": ""},
     {("path", "sub/gone.h"): "fail"}),
    ("found below an unnamed dir", "`inner/x.py`", {"deep/inner/x.py": ""}, {("path", "inner/x.py"): "ok_suffix"}),
    ("build output: parent missing", "`build/bin/app`", {"src/a.c": ""}, {("path", "build/bin/app"): "unresolved"}),
    ("elided directories", "`src/.../kernel.h`", {"src/a/b/kernel.h": ""}, {("path", "src/.../kernel.h"): "ok"}),
    ("inside a submodule", "`third/dep/lib.h`", {".gitmodules": '[submodule "d"]\n\tpath = third/dep\n',
                                                  "third/README": ""},
     {("path", "third/dep/lib.h"): "unresolved"}),
    ("outside the repository", "`../../x.md`", {}, {("path", "../../x.md"): "fail"}),
    ("bare directory name", "`metrics/`", {"pkg/metrics/a.py": ""}, {("path", "metrics/"): "ok_basename"}),
    # links
    ("link outside the repository", "[x](../../x.md)", {}, {("link", "../../x.md"): "fail"}),
    ("percent-encoded link", "[x](docs/my%20file.md)", {"docs/my file.md": ""},
     {("link", "docs/my%20file.md"): "ok"}),
    ("parentheses in a link", "[x](docs/a_(b).md)", {"docs/a_(b).md": ""}, {("link", "docs/a_(b).md"): "ok"}),
    ("reference definition", "[r]: missing.md", {}, {("link", "missing.md"): "fail"}),
    ("reference definition with title", "  [r]: docs/gone.md 'Old'", {"docs/a.md": ""},
     {("link", "docs/gone.md"): "fail"}),
    ("a footnote is not a definition", "[^1]: see docs/gone.md", {"docs/a.md": ""}, {}),
    ("single-quoted title", "[x](docs/gone.md 'title')", {"docs/a.md": ""}, {("link", "docs/gone.md"): "fail"}),
    ("angle-bracket target with a space", "[x](<docs/my file.md>)", {"docs/a.md": ""},
     {("link", "<docs/my file.md>"): "fail"}),
    ("angle-bracket target found", "[x](<docs/my file.md> \"t\")", {"docs/my file.md": ""},
     {("link", "<docs/my file.md>"): "ok"}),
    ("link into a submodule", "[lib](third/dep/lib.h)", {".gitmodules": '[submodule "d"]\n\tpath = third/dep\n',
                                                          "third/README": ""},
     {("link", "third/dep/lib.h"): "unresolved"}),
    ("trailing slash on a file", "`src/pkg/mod.py/`", {"src/pkg/mod.py": ""}, {("path", "src/pkg/mod.py/"): "fail"}),
    ("trailing slash on a file, as a link", "[m](src/pkg/mod.py/)", {"src/pkg/mod.py": ""},
     {("link", "src/pkg/mod.py/"): "fail"}),
    ("trailing slash on a directory", "`src/pkg/`", {"src/pkg/mod.py": ""}, {("path", "src/pkg/"): "ok"}),
    # identifiers
    ("convention word", "Use `CamelCase` and `snake_case`.", {"a.c": ""}, {}),
    ("environment variable", "`OMP_NUM_THREADS`", {"a.c": ""}, {("identifier", "OMP_NUM_THREADS"): "unresolved"}),
    ("CMakeLists.txt is code", "`TOY_USE_GPU`", {"CMakeLists.txt": "option(TOY_USE_GPU x OFF)\n"},
     {("identifier", "TOY_USE_GPU"): "ok"}),
    # shell
    ("control operators", _shell("make all && make test | tee build.log 2>&1"), {"Makefile": "all:\ntest:\n"},
     {("make_target", "all"): "ok", ("make_target", "test"): "ok"}),
    ("prefixes and comments", _shell("sudo env time make all  # build it"), {"Makefile": "all:\n"},
     {("make_target", "all"): "ok"}),
    ("wrapped with a comment", _shell("`make all`  # build"), {"Makefile": "all:\n"},
     {("shell_fence", "`make all`  # build"): "fail", ("make_target", "all"): "ok"}),
    ("text fences are output", f"{FENCE}text\n`echo` ran\n{FENCE}\n", {}, {}),
    # make
    ("make -j value", _shell("make -j 8 all"), {"Makefile": "all:\n"}, {("make_target", "all"): "ok"}),
    ("no makefile", _shell("make all"), {"src/a.c": ""}, {("make_target", "all"): "fail"}),
    ("catch-all rule", _shell("make html"), {"Makefile": "%:\n\tsphinx $@\n"}, {("make_target", "html"): "unresolved"}),
    ("append assignment", _shell("make CFLAGS+=-g"), {"Makefile": "CFLAGS ?= -O2\nall:\n"},
     {("make_var", "CFLAGS+=-g"): "ok"}),
    # cmake
    ("cmake presets", _shell("cmake --preset dev", "cmake --preset=nope"),
     {"CMakeLists.txt": "", "CMakePresets.json": '{"configurePresets": [{"name" : "dev"}]}'},
     {("cmake_preset", "dev"): "ok", ("cmake_preset", "nope"): "fail"}),
    ("cmake built-ins", _shell("cmake -DBUILD_SHARED_LIBS=ON -DTBB_DIR=/x -DPython3_EXECUTABLE=p -DNOPE=1"),
     {"CMakeLists.txt": ""},
     {("cmake_var", "BUILD_SHARED_LIBS"): "ok", ("cmake_var", "TBB_DIR"): "ok",
      ("cmake_var", "Python3_EXECUTABLE"): "ok", ("cmake_var", "NOPE"): "fail"}),
    # pytest
    ("pytest option values", _shell("pytest -k slow_case --cov agent tests/test_x.py"),
     {"tests/test_x.py": "def test_a():\n    pass\n"},
     {("pytest_path", "tests/test_x.py"): "ok"}),
    ("pytest --pyargs modules",
     _shell("python -m pytest -ra --pyargs pkg.tests pkg.tests.test_a::test_x pkg.tests.test_a::test_y"),
     {"src/pkg/__init__.py": "", "src/pkg/tests/__init__.py": "", "src/pkg/tests/test_a.py": "def test_x(): pass\n"},
     {("pytest_module", "pkg.tests"): "ok", ("pytest_module", "pkg.tests.test_a"): "ok",
      ("pytest_node", "pkg.tests.test_a::test_x"): "ok", ("pytest_node", "pkg.tests.test_a::test_y"): "fail"}),
    ("pytest --pyargs: of two copies of a module, the node is read in the one at an import root",
     _shell("pytest --pyargs pkg.tests.test_a::test_new pkg.tests.test_a::test_old"),
     {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "pkg/tests/test_a.py": "def test_new(): pass\n",
      "benchmarks/pkg/tests/test_a.py": "def test_old(): pass\n", "vendor/pkg/tests/test_a.py": "def test_old(): pass\n"},
     {("pytest_node", "pkg.tests.test_a::test_new"): "ok", ("pytest_node", "pkg.tests.test_a::test_old"): "fail"}),
    ("pytest --pyargs module missing",
     _shell("pytest --pyargs pkg.tests.gone numpy.tests nsdir test_gone.py"),
     {"pkg/__init__.py": "", "pkg/tests/__init__.py": "", "xpkg/tests/gone.py": "", "nsdir/test_a.py": ""},
     {("pytest_module", "pkg.tests.gone"): "fail", ("pytest_module", "numpy.tests"): "unresolved",
      ("pytest_path", "nsdir"): "ok", ("pytest_module", "nsdir"): None,
      ("pytest_path", "test_gone.py"): "unresolved", ("pytest_module", "test_gone.py"): None}),
    ("pytest --pyargs module below a deleted subpackage", _shell("pytest --pyargs pkg.tests.test_a pkg.tests.sub.test_b"),
     {"pkg/__init__.py": "", "pkg/other.py": ""},
     {("pytest_module", "pkg.tests.test_a"): "fail", ("pytest_module", "pkg.tests.sub.test_b"): "fail"}),
    ("pytest --pyargs: a package only below another directory is no evidence of a miss",
     _shell("pytest --pyargs pkg.tests.test_x tests.test_y"),
     {"docs/examples/pkg/__init__.py": "", "sub/tests/__init__.py": ""},
     {("pytest_module", "pkg.tests.test_x"): "unresolved", ("pytest_module", "tests.test_y"): "unresolved"}),
    ("pytest --pyargs: a package at a directory above the guidance file, or in its src/", "x",
     {"python/AGENTS.md": _shell("pytest --pyargs pkg.gone pkg2.gone"), "python/pkg/__init__.py": "",
      "python/src/pkg2/__init__.py": ""},
     {("pytest_module", "pkg.gone"): "fail", ("pytest_module", "pkg2.gone"): "fail"}),
    ("pytest --pyargs: a directory with no __init__.py is no module", _shell("pytest --pyargs pkg.tests"),
     {"pkg/__init__.py": "", "pkg/tests/test_a.py": ""},
     {("pytest_module", "pkg.tests"): "fail"}),
    ("dotted name without --pyargs is a path", _shell("pytest pkg.tests"), {"pkg/tests/__init__.py": ""},
     {("pytest_path", "pkg.tests"): "unresolved", ("pytest_module", "pkg.tests"): None}),
    # pre-commit
    ("no pre-commit config", _shell("pre-commit run lint"), {}, {("precommit_hook", "lint"): "fail"}),
    ("odd pre-commit config", _shell("pre-commit run lint"), {".pre-commit-config.yaml": "- not\n- a mapping\n"},
     {("precommit_hook", "lint"): "fail"}),
    # bazel
    ("URLs and comments are not labels", "`https://x.org/a//b`, `// clang-format off`, `//github.com/acme`",
     {"BUILD": ""}, {}),
    ("missing package tree", "`//gone/...`", {"BUILD": ""}, {("bazel_label", "//gone/..."): "fail"}),
    ("args after -- go to the binary", _shell("bazel run //:t -- --config=x.json"), {"BUILD": 'sh_binary(name = "t")\n'},
     {("bazel_label", "//:t"): "ok", ("bazel_config", "x.json"): None}),
    # globs: judged like the plain path they generalise
    ("glob: parent missing", "`build/*.o` `dist/*.whl` `out/<run>/grade.json` `nope/*.py` `*.sh` `./*.log`",
     {"src/a.py": "", "lib/b.py": ""},
     {("path", "build/*.o"): "unresolved", ("path", "dist/*.whl"): "unresolved",
      ("path", "out/<run>/grade.json"): "unresolved", ("path", "nope/*.py"): "unresolved",
      ("path", "*.sh"): "unresolved", ("path", "./*.log"): "unresolved"}),
    ("glob: parent exists", "`src/*.zzz` `src/*.py` `src/**/*.py` `src/**/c.py`", {"src/a.py": "", "src/d/c.py": ""},
     {("path", "src/*.zzz"): "fail", ("path", "src/*.py"): "ok", ("path", "src/**/*.py"): "ok",
      ("path", "src/**/c.py"): "ok"}),
    ("glob: below an unnamed dir", "`config/*.yaml` `config/*.zzz`", {"sub/config/a.yaml": ""},
     {("path", "config/*.yaml"): "ok_suffix", ("path", "config/*.zzz"): "unresolved"}),
    ("glob: a template or a wildcard directory is never a fail", "`src/<name>.zzz` `src/*/x.py` `src/**/*.zzz`",
     {"src/a.py": ""},
     {("path", "src/<name>.zzz"): "unresolved", ("path", "src/*/x.py"): "unresolved",
      ("path", "src/**/*.zzz"): "unresolved"}),
    ("expansions after make", _shell("make test -j $(nproc)", "make $TARGET", "make ${T} all", "make `echo x` all",
                                     "make test_$(ARCH)", 'make "$@" CC=$(which gcc) all'),
     {"Makefile": "all:\ntest:\n\t$(CC)\n"},
     {("make_target", "test"): "ok", ("make_target", "all"): "ok", ("make_target", "$"): None,
      ("make_target", "$TARGET"): None, ("make_target", "test_$"): None, ("make_target", "x`"): None}),
    ("expansions in other adapters",
     _shell("pytest tests/a.py::test_$N tests/$X.py $(git diff --name-only)", "cmake -DFOO_$X=1 -D$V=2 --preset $P ..",
            "bazel build --config=$CFG //:one //:t_$X", "pre-commit run $HOOK"),
     {"tests/a.py": "def test_a(): pass\n", "CMakeLists.txt": "", "BUILD": 'cc_library(name = "one")\n',
      ".bazelrc": "", ".pre-commit-config.yaml": "repos: []\n"},
     {("pytest_path", "tests/a.py"): "ok", ("pytest_node", "tests/a.py::test_$N"): None,
      ("pytest_path", "tests/$X.py"): None, ("cmake_var", "FOO_$X"): None, ("bazel_label", "//:one"): "ok",
      ("bazel_config", "$CFG"): None, ("precommit_hook", "$HOOK"): None}),
    ("pytest parameter id holding ::", _shell("pytest tests/a.py::test_p[a::b] tests/a.py::test_q[a::b]"),
     {"tests/a.py": "def test_p(): pass\n"},
     {("pytest_node", "tests/a.py::test_p[a::b]"): "ok", ("pytest_node", "tests/a.py::test_q[a::b]"): "fail"}),
    ("a label with an expansion in a span", "`//:ab_$X` `//:one`", {"BUILD": 'cc_library(name = "one")\n'},
     {("bazel_label", "//:ab_"): None, ("bazel_label", "//:one"): "ok"}),
    ("a command continued over lines", _shell("make \\", "  all", "make test \\", "  # note", "  nosuch"),
     {"Makefile": "all:\ntest:\n"},
     {("make_target", "all"): "ok", ("make_target", "\\"): None, ("make_target", "nosuch"): None}),
    ("a continued command is still checked", _shell("make test \\", "  nosuch"), {"Makefile": "test:\n"},
     {("make_target", "test"): "ok", ("make_target", "nosuch"): "fail"}),
    ("heredoc bodies are not commands", _shell("cat <<EOF > x", "make nosuch", "EOF", "cat <<-'EOT'", "\tpytest nope.py",
                                               "\tEOT", "make all"),
     {"Makefile": "all:\n"},
     {("make_target", "nosuch"): None, ("pytest_path", "nope.py"): None, ("make_target", "all"): "ok"}),
    ("a here-string, a shift and a quoted << open no heredoc",
     _shell("grep x <<< foo", "make bilda", "echo $((1<<3))", "make bildb", 'echo "a << b"', "make bildc",
            "cat <<EOF", "make nosuch", "EOF", "make all"),
     {"Makefile": "all:\n"},
     {("make_target", "bilda"): "fail", ("make_target", "bildb"): "fail", ("make_target", "bildc"): "fail",
      ("make_target", "nosuch"): None, ("make_target", "all"): "ok"}),
    ("single-quoted target name", "`//:one` `//:nope`", {"BUILD": "cc_library(name = 'one')\n"},
     {("bazel_label", "//:one"): "ok", ("bazel_label", "//:nope"): "fail"}),
    ("genrule output is a label", "`//:gen.h` `//:nope`",
     {"BUILD": 'genrule(name = "g", outs = ["gen.h"], cmd = "x")\n'},
     {("bazel_label", "//:gen.h"): "ok", ("bazel_label", "//:nope"): "fail"}),
    ("BUILD.bazel wins over BUILD", "`//:b`", {"BUILD": 'cc_library(name = "a")\n',
                                               "BUILD.bazel": 'cc_library(name = "b")\n'},
     {("bazel_label", "//:b"): "ok"}),
    ("computed target names", "`//a:t_a` `//a:zz` `//b:t_a` `//c:t_a` `//d:t_a` `//e:t_a`",
     {"a/BUILD": '[cc_test(name = "t_" + s) for s in ["a"]]\n', "b/BUILD": 'cc_test(name = "t_%s" % s)\n',
      "c/BUILD": 'N = "t_a"\ncc_test(name = N)\n', "d/BUILD": 'cc_test(name = "x" if c else "y")\n',
      "e/BUILD": 'load("//:m.bzl", "gen")\n[gen(n) for n in ["a"]]\n', "BUILD": ""},
     {("bazel_label", f"//{p}:t_a"): "unresolved" for p in "abcde"} | {("bazel_label", "//a:zz"): "unresolved"}),
    ("a typo stays fail when every name is literal", "`//:one` `//:one_tset`",
     {"BUILD": '# Needed for tests in CI\nfilegroup(name = "f", srcs = [x for x in glob(["*.cc"])])\n'
               'cc_library(name = "one")\n'},
     {("bazel_label", "//:one"): "ok", ("bazel_label", "//:one_tset"): "fail"}),
    ("a name in a comment is not a name", "`//:b`", {"BUILD": 'cc_library(name = "a")  # name = "b"\n'},
     {("bazel_label", "//:b"): "fail"}),
    ("exports_files(glob) may export any file", "`//:LICENSE` `//:x`",
     {"BUILD": 'exports_files(glob(["*"]))\ncc_library(name = "x")\n'},
     {("bazel_label", "//:LICENSE"): "unresolved", ("bazel_label", "//:x"): "ok"}),
    ("exports_files lists every file", "`//:LICENSE` `//:NOTICE` `//:OTHER`",
     {"BUILD": 'exports_files(["LICENSE", "NOTICE"])\n'},
     {("bazel_label", "//:LICENSE"): "ok", ("bazel_label", "//:NOTICE"): "ok", ("bazel_label", "//:OTHER"): "fail"}),
    ("exports_files with metadata argument parses only positional", "`//:notice`",
     {"BUILD": 'exports_files(["LICENSE"], licenses = ["notice"])\n', "LICENSE": ""},
     {("bazel_label", "//:notice"): "fail"}),
    ("comments between def parameters",
     f"{FENCE}starlark\nm(\n    name = \"x\",\n    srcs = [],\n    flags = {{}},\n    nme = 1,\n)\n{FENCE}\n",
     {"BUILD": "", "a.bzl": 'def m(\n    name,  # the target (required):\n    srcs = [],  # inputs, if any\n'
                            '    flags = {"a": 1, "b": 2},\n    note = "#a, b",\n):\n    pass\n'},
     {("starlark_kwarg", "m(srcs=)"): None, ("starlark_kwarg", "m(flags=)"): None,
      ("starlark_kwarg", "m(nme=)"): "fail"}),
    ("macro defined twice", f"{FENCE}starlark\nm(\n    name = \"x\",\n    extra = 1,\n)\n{FENCE}\n",
     {"BUILD": "", "a.bzl": "def m(name):\n    pass\n", "b.bzl": "def m(name, extra):\n    pass\n"},
     {("starlark_kwarg", "m(extra=)"): "unresolved"}),
    ("definitions in docstrings are not public", f"{FENCE}starlark\nload(\"//:defs.bzl\", \"removed\", \"actual\")\n{FENCE}\n",
     {"BUILD": "", "defs.bzl": '"""Example module.\n\ndef removed(name):\n    pass\n\ngone = rule()\n"""\n\ndef actual(name):\n    pass\n'},
     {("starlark_load", "removed from //:defs.bzl"): "fail", ("starlark_load", "actual from //:defs.bzl"): "ok"}),
    # links to this repository by URL: a ref may hold slashes
    ("slash branch, file found", f"[a]({U}/blob/release/1.0/src/a.py)", {"src/a.py": ""},
     {("link", f"{U}/blob/release/1.0/src/a.py"): "ok"}),
    ("slash branch, file gone", f"[a]({U}/blob/release/1.0/src/gone.py)", {"src/a.py": ""},
     {("link", f"{U}/blob/release/1.0/src/gone.py"): "fail"}),
    ("slash branch, directory", f"[a]({U}/tree/feature/x/src/pkg#readme)", {"src/pkg/a.py": ""},
     {("link", f"{U}/tree/feature/x/src/pkg#readme"): "ok"}),
    ("refs/heads/ prefix", f"[a]({U}/blob/refs/heads/main/src/a.py)", {"src/a.py": ""},
     {("link", f"{U}/blob/refs/heads/main/src/a.py"): "ok"}),
    ("tree link may name only a slash branch", f"[a]({U}/tree/release/1.0)", {"src/a.py": ""},
     {("link", f"{U}/tree/release/1.0"): "unresolved"}),
    ("dev/ and v2/ prefixes are branch names", f"[a]({U}/blob/dev/alice/src/a.py) [b]({U}/blob/v2/hotfix/src/a.py)",
     {"src/a.py": ""},
     {("link", f"{U}/blob/dev/alice/src/a.py"): "ok", ("link", f"{U}/blob/v2/hotfix/src/a.py"): "ok"}),
    ("a version tag is one segment", f"[a]({U}/blob/v1.2.3/src/a.py)", {"a.py": ""},
     {("link", f"{U}/blob/v1.2.3/src/a.py"): "fail"}),
    ("default branch is one segment", f"[a]({U}/blob/main/src/a.py)", {"a.py": ""},
     {("link", f"{U}/blob/main/src/a.py"): "fail"}),
    ("tree link on a default branch, gone", f"[a]({U}/tree/main/docs)", {"src/a.py": ""},
     {("link", f"{U}/tree/main/docs"): "fail"}),
    ("tree link to a ref alone is no claim", f"[a]({U}/tree/main/)", {"src/a.py": ""}, {}),
    # relative paths after a cd
    ("pytest path after cd", _shell("cd sub && pytest tests/test_y.py"),
     {"tests/test_x.py": "", "sub/tests/test_y.py": ""}, {("pytest_path", "tests/test_y.py"): "ok"}),
    ("pytest path gone after cd", _shell("cd sub", "pytest tests/test_gone.py"),
     {"tests/test_x.py": "", "sub/tests/test_y.py": ""}, {("pytest_path", "tests/test_gone.py"): "fail"}),
    ("script path after cd and back", _shell("cd sub && cd .. && python tools/run.py"),
     {"tools/run.py": "", "sub/x.py": ""}, {("script_path", "tools/run.py"): "ok"}),
    ("a subshell cd ends at the paren", _shell("(cd sub && make); pytest tests/test_x.py"),
     {"tests/test_x.py": "", "sub/Makefile": "all:\n"}, {("pytest_path", "tests/test_x.py"): "ok"}),
    ("cd into a directory the tree lacks", _shell("cd build && python ../tools/run.py"), {"tools/run.py": ""},
     {("script_path", "../tools/run.py"): "unresolved"}),
    ("cd to a variable", _shell("cd $REPO && pytest tests/test_y.py"), {"tests/test_x.py": ""},
     {("pytest_path", "tests/test_y.py"): "unresolved"}),
    ("cd after ||", _shell("cd sub || cd other", "pytest tests/test_x.py"), {"tests/test_x.py": "", "sub/a": ""},
     {("pytest_path", "tests/test_x.py"): "unresolved"}),
    ("the next fence starts at the file again", _shell("cd sub") + _shell("pytest tests/test_x.py"),
     {"tests/test_x.py": "", "sub/a": ""}, {("pytest_path", "tests/test_x.py"): "ok"}),
    ("a path named from the root is gated, a bare one is not", "`config/gone.yaml` `dokimi/config/gone.yaml`",
     {"dokimi/config/x.yaml": ""},
     {("path", "config/gone.yaml"): "unresolved", ("path", "dokimi/config/gone.yaml"): "fail"}),
]


@pytest.mark.parametrize("name, agents, files, expected", CASES, ids=[c[0] for c in CASES])
def test_rule(tmp_path, name, agents, files, expected):
    got = _verdicts(_scan(tmp_path, agents + "\n", files))
    if not expected:
        assert got == {}, got
    assert {k: got.get(k) for k in expected} == expected, got


def test_a_path_into_a_submodule_says_so(tmp_path):
    payload = _scan(tmp_path, "`third/dep/lib.h`\n", {".gitmodules": '[submodule "d"]\n\tpath = third/dep\n',
                                                     "third/README": ""})
    assert "inside the submodule third/dep" in payload["claims"][0]["why"]


def test_a_missing_pyargs_module_names_the_nearest_package(tmp_path):
    payload = _scan(tmp_path, _shell("pytest --pyargs pkg.tests.gone pkg.tests.sub.test_b lib.tests.test_a "
                                     "other.tests.test_c"),
                    {"src/pkg/__init__.py": "", "src/pkg/tests/__init__.py": "",
                     "lib/__init__.py": "", "vendor/lib/tests/__init__.py": "", "docs/x/other/__init__.py": ""})
    why = {c["claim"]: c["why"] for c in payload["claims"]}
    assert why["pkg.tests.gone"] == "package pkg.tests (src/pkg/tests/) has no module gone"
    assert why["pkg.tests.sub.test_b"] == "package pkg.tests (src/pkg/tests/) has no module sub.test_b"
    assert why["lib.tests.test_a"] == "package lib (lib/) has no module tests.test_a"  # not the vendored copy
    assert why["other.tests.test_c"] == ("no module other.tests.test_c at an import root; package other is only "
                                         "in docs/x/other/, which may not be the one imported")


def test_no_claim_for_options_or_operators(tmp_path):
    got = _verdicts(_scan(tmp_path, _shell("make -j 8 all > log && pytest -k slow_case tests/"),
                          {"Makefile": "all:\n", "tests/a.py": ""}))
    assert ("make_target", "8") not in got and ("make_target", "log") not in got
    assert ("pytest_path", "slow_case") not in got


def test_a_github_action_reference_is_not_a_path(tmp_path):
    got = _verdicts(_scan(tmp_path, "Pin `actions/checkout@v6` and `actions/upload-artifact@ea165f8d...`; "
                          "see `actions/old.yml` and `docs/a@b/x.md`.\n", {"actions/new.yml": ""}))
    assert not any("@v6" in claim or "@ea165f8d" in claim for _, claim in got)
    assert got[("path", "actions/old.yml")] == "fail"          # a real path in that directory still is one
    assert ("path", "docs/a@b/x.md") in got                    # an `@` mid-path is not a ref


def test_a_file_name_with_an_at_sign_is_still_a_path(tmp_path):
    got = _verdicts(_scan(tmp_path, "Use `assets/icon@2x.png`, `assets/logo@3x.svg` and `tools/run@v2`.\n",
                          {"assets/logo@3x.svg": "", "tools/run@v2": ""}))
    assert got[("path", "assets/icon@2x.png")] == "fail"       # broken: the directory has other files
    assert got[("path", "assets/logo@3x.svg")] == "ok"
    assert got[("path", "tools/run@v2")] == "ok"               # ref-shaped, but it is in the tree


def test_links_inside_code_spans_are_not_links(tmp_path):
    got = _verdicts(_scan(tmp_path, "Write `[text](docs/guide.md)` in a page.\n"))
    assert not any(kind == "link" for kind, _ in got)


def test_tox_ini_selects_pytest(tmp_path):
    payload = _scan(tmp_path, "x\n", {"tox.ini": "[tox]\n"}, build_systems=())
    assert payload["adapters"] == ["pytest"] and payload["adapters_detected"]


def test_static_claim_without_claim_text_replaces_nothing(tmp_path):
    tree = _git_repo(tmp_path / "r", {"AGENTS.md": "`src/a.h`\n", "src/a.h": "", "ev/tasks/t/task.json": "{}"})
    static = {"schema": "repo_static.v1", "claims": [{"file": "AGENTS.md", "line": 1, "status": "fail"}]}
    payload = scan_repo(tree, _manifest(), static)
    assert payload["static"]["superseded"] == 0
    assert sorted(c["source"] for c in payload["claims"]) == ["framework", "repository"]


def test_link_by_url_and_by_path_is_one_claim(tmp_path):
    url = "https://github.com/acme/case/blob/main/src/a.h"
    tree = _git_repo(tmp_path / "r", {"AGENTS.md": f"[a]({url})\n", "src/a.h": "", "ev/tasks/t/task.json": "{}"})
    static = {"schema": "repo_static.v1", "claims": [{"file": "AGENTS.md", "line": 1, "status": "ok",
                                                      "claim": "/src/a.h"}]}
    assert scan_repo(tree, _manifest(), static)["static"]["superseded"] == 1


def _cli(tmp_path, root: Path, *extra: str) -> int:
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "schema": "repo_manifest.v1", "id": "case", "eval_dir": "ev", "guidance": {"globs": ["**/AGENTS.md"]},
        "entry_points": {"grade": "true"}, "tasks": "ev/tasks"}), encoding="utf-8")
    args = build_parser().parse_args(["repo", "scan", str(root), "--manifest-file", str(manifest), *extra])
    try:
        args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_cli_exit_codes(tmp_path, capsys):
    root = tmp_path / "r"
    _git_repo(root, {"AGENTS.md": "`src/a.h`\n", "src/a.h": "", "ev/tasks/t/task.json": "{}"})
    assert _cli(tmp_path, root, "--fail-on-fail") == 0
    for doc in ('{"schema": "repo_static.v1", "claims": [{"file": "AGENTS.md"}]}', "[1, 2]", '"text"'):
        bad = tmp_path / "static.json"
        bad.write_text(doc, encoding="utf-8")
        assert _cli(tmp_path, root, "--static", str(bad), "--json") == 1
        assert "--static output not merged" in capsys.readouterr().err

    empty = tmp_path / "empty"
    _git_repo(empty, {"README.md": "no guidance here\n", "ev/tasks/t/task.json": "{}"})
    assert _cli(tmp_path, empty) == 0
    assert "warning: no guidance file to scan" in capsys.readouterr().out
    assert _cli(tmp_path, empty, "--fail-on-fail") == 1


def _linked_repo(root: Path, files: dict[str, str], links: dict[str, str]) -> GitTree:
    """A repository with ``links`` (path -> link text) committed as symlinks."""
    for rel, text in links.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).symlink_to(text)
    return _git_repo(root, files)


def _both(tmp_path, files, links, **manifest):
    root = tmp_path / "r"
    tree = _linked_repo(root, {"ev/tasks/t/task.json": "{}", **files}, links)
    m = _manifest(**manifest)
    return scan_repo(tree, m), scan_repo(WorkTree(root), m)


def test_symlinked_guidance_same_counts_worktree_and_gittree(tmp_path):
    agents = "[a](docs/a.md) [b](docs/gone.md) `src/x.py`\n"
    from_git, from_dir = _both(tmp_path, {"AGENTS.md": agents, "docs/a.md": "", "src/x.py": ""},
                               {"CLAUDE.md": "AGENTS.md", "sub/AGENTS.md": "../AGENTS.md"},
                               also_scan=["CLAUDE.md"])
    assert from_git["counts"] == from_dir["counts"] == {**from_git["counts"], "ok": 2, "fail": 1, "total": 3}
    assert from_git["claims"] == from_dir["claims"]
    assert not from_git["incomplete"] and not from_dir["incomplete"]


def test_symlinked_guidance_alone_is_followed(tmp_path):
    # Only the link matches the manifest: the file it names is scanned under the link's name.
    from_git, from_dir = _both(tmp_path, {"docs/GUIDE.md": "[b](gone.md)\n"}, {"AGENTS.md": "docs/GUIDE.md"})
    assert from_git["claims"] == from_dir["claims"]
    assert [(c["file"], c["status"]) for c in from_git["claims"]] == [("AGENTS.md", "fail")]


@pytest.mark.parametrize("link, why", [("NOWHERE.md", "links to nothing"), ("../outside.md", "escapes")])
def test_dangling_guidance_symlink_reported(tmp_path, link, why):
    (tmp_path / "outside.md").write_text("[x](gone.md)\n", encoding="utf-8")
    for payload in _both(tmp_path, {"AGENTS.md": "x\n"}, {"CLAUDE.md": link}, also_scan=["CLAUDE.md"]):
        warning = next(w for w in payload["warnings"] if "CLAUDE.md" in w)
        assert warning.startswith("guidance file not scanned: CLAUDE.md") and why in warning, warning
        assert payload["incomplete"]
        assert payload["counts"]["total"] == 0


@pytest.mark.parametrize("body, why", [("[x](gone.md)\n" + "x" * (2 * 1024 * 1024), "over the 2 MB"),
                                       ("[x](gone.md)\n\0", "binary")], ids=["oversize", "binary"])
def test_oversize_or_binary_guidance_warns(tmp_path, body, why):
    for payload in _both(tmp_path, {"AGENTS.md": body}, {}):
        assert any(w.startswith("guidance file not scanned: AGENTS.md") and why in w for w in payload["warnings"])
        assert payload["incomplete"]


def test_link_through_a_symlinked_directory(tmp_path):
    for payload in _both(tmp_path, {"AGENTS.md": "[l](linkdir/core.py) [m](linkdir/gone.py)\n",
                                    "src/pkg/core.py": ""}, {"linkdir": "src/pkg"}):
        assert _verdicts(payload) == {("link", "linkdir/core.py"): "ok", ("link", "linkdir/gone.py"): "fail"}


def test_gate_fails_when_globbed_guidance_deleted(tmp_path, monkeypatch, capsys):
    """The self-scan manifest run on this repository without its root AGENTS.md exits 1."""
    import dokimi.repos as repos

    framework = Path(__file__).resolve().parents[1]

    class WithoutAgentsMd(WorkTree):
        def files(self) -> list[str]:
            return [f for f in super().files() if f != "AGENTS.md"]

    monkeypatch.setattr(repos, "open_source", lambda source, cache=None: WithoutAgentsMd(source.location))
    manifest = framework / "benchmarks" / "self-scan.repo-eval.yaml"
    args = build_parser().parse_args(["repo", "scan", str(framework.parent), "--manifest-file", str(manifest),
                                      "--fail-on-fail"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert "guidance/also_scan matches no file: AGENTS.md" in capsys.readouterr().out


def test_unreadable_guidance_that_detect_also_reads_keeps_its_warning(tmp_path):
    # detect() reads setup.cfg before the guidance files are preloaded: its failed read must still be reported
    for payload in _both(tmp_path, {"AGENTS.md": "x\n"}, {"setup.cfg": "nowhere.cfg"}, build_systems=[],
                         also_scan=["setup.cfg"]):
        warning = next((w for w in payload["warnings"] if "setup.cfg" in w), "")
        assert warning.startswith("guidance file not scanned: setup.cfg") and "links to nothing" in warning, payload
        assert payload["incomplete"]
