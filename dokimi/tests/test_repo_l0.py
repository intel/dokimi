# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the L0 scan (repos/l0): generic claim checks, build-system adapters, static merge."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dokimi.artifacts import load_artifact
from dokimi.cli import build_parser
from dokimi.repos import parse_manifest
from dokimi.repos.l0 import scan_repo
from dokimi.repos.source import GitTree, WorkTree

MANIFEST = {
    "schema": "repo_manifest.v1",
    "id": "toy",
    "source": {"url": "https://github.com/acme/toy"},
    "eval_dir": "dev/evals",
    "guidance": {"globs": ["**/AGENTS.md"], "also_scan": ["CONTRIBUTING.md"]},
    "entry_points": {"grade": "true"},
    "tasks": "dev/evals/tasks",
    "build_systems": ["bazel", "make", "cmake", "pytest", "pre-commit"],
}

FENCE = "```"
AGENTS = f"""# Toy
See [the header](src/a.h), [gone](missing.md), [blob](https://github.com/acme/toy/blob/main/src/a.h),
[blob gone](https://github.com/acme/toy/tree/main/nowhere), [site](https://example.com/x) and [top](#toy).
Paths: `src/a.h`, `a.h`, `src/*.h`, `docs/*.xyz`, `src/*.xyz`, `src/missing.h`, `out/gen.h`, `inner/x.py`, `Makefile`, `<out>/a.h`, `{{run_dir}}/x.json`.
Names: `ToyWidget`, `GhostWidget`, `OnlyInDocs`.
Labels: `//lib:core`, `//lib:nope`, `//pkg2:gen_extra`, `//nopkg:x`, `@other//x:y`, `@toy//lib:core`,
`//lib/...`, `//conditions:default`.

{FENCE}bash
bazel build --config=opt //lib:core
bazel test --config=nope //lib:core
`make all`
make lib_foo
make ghost
make VERBOSE=1
cmake -S . -B build -DTOY_GPU=ON -DCMAKE_BUILD_TYPE=Release -DNOPE=1
pytest tests/test_x.py::test_one
python -m pytest tests/test_x.py::test_two
pre-commit run clang-format
pre-commit run ghost
$ python3 tools/missing.py
{FENCE}

{FENCE}starlark
my_macro(
    name = "x",
    anything = 1,
)
strict(
    name = "y",
    bogus = 2,
)
unknown_rule(name = "z")
{FENCE}
"""

FILES = {
    "AGENTS.md": AGENTS,
    "CONTRIBUTING.md": "Run `make all`.\n",
    "README.md": "OnlyInDocs\n",
    "MODULE.bazel": 'module(name = "toy")\n',
    ".bazelrc": "build:opt --copt=-O2\ntry-import %workspace%/user.bazelrc\n",
    "user.bazelrc": "build:fast --jobs=8\n",
    "BUILD": "",
    "defs.bzl": "def my_macro(name, srcs = [], **kwargs):\n    pass\n\ndef strict(name, deps = []):\n    pass\n",
    "lib/BUILD": 'cc_library(name = "core")\n',
    "pkg2/BUILD": 'load("//:defs.bzl", "my_macro")\nmy_macro(name = "gen")\n',
    "Makefile": "all:\n\techo all\nlib_%:\n\techo $@\n$(OBJ):\n\techo\n",
    "CMakeLists.txt": 'option(TOY_GPU "gpu" OFF)\n',
    ".pre-commit-config.yaml": "repos:\n- repo: local\n  hooks:\n  - id: clang-format\n",
    "tests/test_x.py": "def test_one():\n    pass\n",
    "pyproject.toml": "[tool.pytest.ini_options]\n",
    "src/a.h": "struct ToyWidget {};\n",
    "deep/inner/x.py": "",
    "dev/evals/tasks/t/task.json": "{}",
}


def _repo(root: Path, files: dict[str, str] = FILES) -> str:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "toy")
    return git("rev-parse", "HEAD")


def _statuses(payload: dict) -> dict[tuple[str, str], str]:
    return {(c["kind"], c["claim"]): c["status"] for c in payload["claims"]}


@pytest.fixture
def toy(tmp_path):
    root = tmp_path / "toy"
    sha = _repo(root)
    return root, sha


def test_generic_checks_and_adapters(toy):
    root, sha = toy
    payload = scan_repo(GitTree(root / ".git", sha), parse_manifest(MANIFEST))
    got = _statuses(payload)
    expected = {
        ("link", "src/a.h"): "ok",
        ("link", "missing.md"): "fail",
        ("link", "https://github.com/acme/toy/blob/main/src/a.h"): "ok",
        ("link", "https://github.com/acme/toy/tree/main/nowhere"): "fail",
        ("path", "src/a.h"): "ok",
        ("path", "a.h"): "ok_basename",
        ("path", "src/*.h"): "ok",
        ("path", "docs/*.xyz"): "unresolved",  # no docs/: like out/gen.h, a build may create it
        ("path", "src/*.xyz"): "fail",  # src/ exists and holds no such file: like src/missing.h
        ("path", "src/missing.h"): "fail",  # src/ exists, so the file is gone, not yet to be built
        ("path", "out/gen.h"): "unresolved",  # no out/ anywhere: a build may create it
        ("path", "inner/x.py"): "ok_suffix",  # deep/inner/x.py
        ("path", "Makefile"): "ok",
        ("path", "<out>/a.h"): "ok",  # a.h exists below some directory
        ("path", "{run_dir}/x.json"): "unresolved",  # the root is not in the tree
        ("identifier", "ToyWidget"): "ok",
        ("identifier", "GhostWidget"): "fail",
        ("identifier", "OnlyInDocs"): "fail",  # prose is not code
        ("bazel_label", "//lib:core"): "ok",
        ("bazel_label", "//lib:nope"): "fail",
        ("bazel_label", "//pkg2:gen_extra"): "unresolved",  # pkg2/BUILD calls a macro
        ("bazel_label", "//nopkg:x"): "fail",
        ("bazel_label", "@other//x:y"): "unresolved",
        ("bazel_label", "@toy//lib:core"): "ok",
        ("bazel_label", "//lib/..."): "ok",
        ("bazel_config", "opt"): "ok",
        ("bazel_config", "nope"): "fail",
        ("shell_fence", "`make all`"): "fail",
        ("make_target", "all"): "ok",
        ("make_target", "lib_foo"): "ok",
        ("make_target", "ghost"): "unresolved",  # $(OBJ) rules are not expanded
        ("make_var", "VERBOSE=1"): "fail",
        ("cmake_var", "TOY_GPU"): "ok",
        ("cmake_var", "CMAKE_BUILD_TYPE"): "ok",
        ("cmake_var", "NOPE"): "fail",
        ("pytest_path", "tests/test_x.py"): "ok",
        ("pytest_node", "tests/test_x.py::test_one"): "ok",
        ("pytest_node", "tests/test_x.py::test_two"): "fail",
        ("precommit_hook", "clang-format"): "ok",
        ("precommit_hook", "ghost"): "fail",
        ("script_path", "tools/missing.py"): "unresolved",
        ("starlark_macro", "my_macro"): "ok",
        ("starlark_kwarg", "my_macro(anything=)"): "unresolved",
        ("starlark_kwarg", "strict(bogus=)"): "fail",
        ("starlark_macro", "unknown_rule"): "unresolved",
    }
    assert {k: got.get(k) for k in expected} == expected
    assert not any(k[1] in ("https://example.com/x", "#toy", "//conditions:default") for k in got)
    assert payload["files"] == ["AGENTS.md", "CONTRIBUTING.md"]
    assert payload["counts"]["total"] == len(payload["claims"])
    assert payload["by_source"]["repository"] is None and payload["static"] is None
    line = next(c["line"] for c in payload["claims"] if c["claim"] == "nope")
    assert AGENTS.splitlines()[line - 1].startswith("bazel test --config=nope")


def test_work_tree_and_git_tree_agree(toy):
    root, sha = toy
    manifest = parse_manifest(MANIFEST)
    from_git = scan_repo(GitTree(root / ".git", sha), manifest)
    from_dir = scan_repo(WorkTree(root), manifest)
    assert from_git["claims"] == from_dir["claims"]


def test_adapters_are_detected_when_not_declared(toy):
    root, sha = toy
    raw = {k: v for k, v in MANIFEST.items() if k != "build_systems"}
    payload = scan_repo(GitTree(root / ".git", sha), parse_manifest(raw))
    assert payload["adapters_detected"] is True
    assert payload["adapters"] == ["bazel", "make", "cmake", "pytest", "pre-commit"]


def test_undeclared_adapter_makes_no_claims(toy):
    root, sha = toy
    payload = scan_repo(GitTree(root / ".git", sha), parse_manifest(dict(MANIFEST, build_systems=["make"])))
    kinds = {c["kind"] for c in payload["claims"]}
    assert "make_target" in kinds
    assert not kinds & {"bazel_label", "bazel_config", "cmake_var", "pytest_node", "precommit_hook"}


def test_static_output_is_merged_and_replaces_framework_claims(toy):
    root, sha = toy
    line = next(n for n, text in enumerate(AGENTS.splitlines(), 1) if text.startswith("Paths:"))
    static = {"schema": "repo_static.v1", "files": ["AGENTS.md"],
              "claims": [{"file": "AGENTS.md", "line": line, "status": "fail", "kind": "path",
                          "claim": "src/a.h", "why": "repository policy"},
                         {"file": "AGENTS.md", "line": 1, "status": "ok_policy", "claim": "Toy"}]}
    payload = scan_repo(GitTree(root / ".git", sha), parse_manifest(MANIFEST), static)
    assert payload["static"] == {"errors": [], "claims": 2, "superseded": 1, "extra": {"files": ["AGENTS.md"]}}
    replaced = [c for c in payload["claims"] if c["claim"] == "src/a.h" and c["line"] == line]
    assert [(c["source"], c["status"]) for c in replaced] == [("repository", "fail")]
    assert payload["by_source"]["repository"] == {"ok": 1, "unresolved": 0, "fail": 1, "total": 2}
    framework = payload["by_source"]["framework"]["total"]
    assert payload["counts"]["total"] == framework - 1 + 2


def test_invalid_static_output_is_reported_not_merged(toy):
    root, sha = toy
    payload = scan_repo(GitTree(root / ".git", sha), parse_manifest(MANIFEST),
                        {"schema": "repo_static.v1", "claims": [{"file": "AGENTS.md", "status": "bad"}]})
    assert payload["static"]["errors"] and payload["static"]["claims"] == 0
    assert all(c["source"] == "framework" for c in payload["claims"])


def test_scan_command_writes_a_valid_artifact(toy, tmp_path, capsys):
    root, sha = toy
    manifest_file = tmp_path / "m.json"
    manifest_file.write_text(json.dumps(MANIFEST), encoding="utf-8")
    out = tmp_path / "readiness.json"
    argv = ["repo", "scan", f"{root}@{sha}", "--manifest-file", str(manifest_file), "--out", str(out)]
    args = build_parser().parse_args(argv)
    args.func(args)
    printed = capsys.readouterr().out
    assert "toy: 2 guidance files" in printed and "FAIL AGENTS.md:" in printed
    data = load_artifact("repo_readiness", out)
    assert data["schema_version"] == "repo_readiness.v1" and data["repo"]["sha"] == sha

    args = build_parser().parse_args(argv + ["--fail-on-fail"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1


def test_read_many_fetches_missing_blobs_of_a_partial_clone(toy, tmp_path):
    root, sha = toy
    subprocess.run(["git", "-C", str(root), "config", "uploadpack.allowFilter", "true"], check=True)
    clone = tmp_path / "partial.git"
    subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:none", f"file://{root}", str(clone)],
                   check=True, capture_output=True)
    packs = lambda: len(list((clone / "objects" / "pack").glob("*.pack")))  # noqa: E731
    before = packs()
    tree = GitTree(clone, sha)
    texts = tree.read_many(["src/a.h", "AGENTS.md", "absent.txt"])
    assert texts == {"src/a.h": FILES["src/a.h"], "AGENTS.md": AGENTS}
    assert packs() == before + 1  # one fetch for both blobs, not one per blob or per tree entry
    tree.read_many(["src/a.h"])
    assert packs() == before + 1  # nothing left to fetch


def test_read_many_returns_every_path_of_a_shared_blob(tmp_path):
    root = tmp_path / "dup"
    root.mkdir()
    for name, text in {"a.md": "same\n", "b.md": "same\n", "e1.py": "", "e2.py": ""}.items():
        (root / name).write_text(text)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "x"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    texts = GitTree(root / ".git", sha).read_many(["a.md", "b.md", "e1.py", "e2.py"])
    assert texts == {"a.md": "same\n", "b.md": "same\n", "e1.py": "", "e2.py": ""}


def test_git_text_reads_stay_strict_for_a_blob_that_is_not_utf8(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"guide caf\xe9\nline\n")
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "bad")
    with pytest.raises(UnicodeDecodeError):
        GitTree(root / ".git", git("rev-parse", "HEAD")).read_text("AGENTS.md")
