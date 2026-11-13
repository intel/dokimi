# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval check`, `repo run` and `eval tasks generate --from replay|prs` exit 2 on a source the user mistyped.

A source `RepoSource.parse` refuses, or a pinned local path inside a repository, is bad input, as with
`--from graph`; a source that parses but cannot be opened (clone, fetch, revision) stays 1.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from dokimi import cli
from tests.cli_refusal import refusal

PIN = "a" * 40
PRS = '[{"number": 7, "reviewThreads": {"nodes": []}, "mergeCommit": null}]'


def _run(monkeypatch, capsys, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    with refusal() as outcome:
        cli.main()
    return outcome.code, capsys.readouterr().err


def _command(name, source, tmp_path):
    prs = tmp_path / "prs.json"
    prs.write_text(PRS, encoding="utf-8")
    out = str(tmp_path / "out")
    return {
        "check": ["eval", "check", source, "--out-dir", out],
        "run": ["repo", "run", source, "--batch", "b", "--out-dir", out, "--arm", "guidance:none", "--model", "m", "--bin", sys.executable],
        "replay": ["eval", "tasks", "generate", source, "--from", "replay", "--threads", str(prs), "--out-dir", out],
        "prs": ["eval", "tasks", "generate", source, "--from", "prs", "--prs", str(prs), "--out-dir", out],
    }[name]


@pytest.mark.parametrize("name", ["check", "run"])
@pytest.mark.parametrize(("source", "message"), [
    ("{missing}", "Error: local source is not a directory: {missing}"),
    ("https://example.invalid/r", "Error: a remote source must be pinned to a revision: https://example.invalid/r@<sha>"),
])
def test_check_and_run_refuse_a_source_that_does_not_parse_with_exit_2(name, source, message, tmp_path, monkeypatch,
                                                                       capsys):
    missing = str(tmp_path / "missing")
    code, err = _run(monkeypatch, capsys, *_command(name, source.format(missing=missing), tmp_path))
    assert code == 2
    assert err.strip() == message.format(missing=missing)


def _repo_with_sub(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "f.txt").write_text("x", encoding="utf-8")
    # gpgsign is off here: a host with a global commit.gpgsign=true would refuse the commit.
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    return repo, sha


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_pinned_subdirectory_of_a_repository_exits_2(name, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    code, err = _run(monkeypatch, capsys, *_command(name, f"{repo / 'sub'}@{sha}", tmp_path))
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("flag", ["--threads", "--prs"])
@pytest.mark.parametrize("extra", ["missing input", "empty prompt"])
def test_pinned_subdirectory_is_refused_before_the_input_files_are_read(flag, extra, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    source = f"{repo / 'sub'}@{sha}"
    out = str(tmp_path / "out")
    kind = "replay" if flag == "--threads" else "prs"
    argv = ["eval", "tasks", "generate", source, "--from", kind, flag, str(tmp_path / "missing.json"), "--out-dir", out]
    if extra == "empty prompt":
        (tmp_path / "prs.json").write_text(PRS, encoding="utf-8")
        argv[argv.index(str(tmp_path / "missing.json"))] = str(tmp_path / "prs.json")
        argv += ["--prompt-file", str(empty)]
    code, err = _run(monkeypatch, capsys, *argv)
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize(("flag", "value"), [("--arm", "guidance:bogus"), ("--bin", "/no/such/bin")])
def test_run_refuses_a_pinned_subdirectory_before_the_arms_and_the_harness(flag, value, tmp_path, monkeypatch,
                                                                           capsys):
    repo, sha = _repo_with_sub(tmp_path)
    argv = _command("run", f"{repo / 'sub'}@{sha}", tmp_path)
    argv[argv.index(flag) + 1] = value
    code, err = _run(monkeypatch, capsys, *argv)
    assert code == 2
    assert err.strip() == f"Error: {repo / 'sub'} is 'sub/' inside a repository; give its root"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
@pytest.mark.parametrize(("manifest", "code", "message"), [
    ("../outside.yaml", 2, "Error: path escapes the repository: ../outside.yaml"),
    ("missing.yaml", 1, "✗ missing.yaml: invalid repo_manifest.v1:"),
])
def test_manifest_path_leaving_the_tree_exits_2_and_a_missing_manifest_exits_1(name, manifest, code, message,
                                                                              tmp_path, monkeypatch, capsys):
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "-C", str(tree), "init", "-q"], check=True, capture_output=True)  # `repo run` pins arms in a git dir
    code_got, err = _run(monkeypatch, capsys, *_command(name, str(tree), tmp_path), "--manifest", manifest)
    assert code_got == code
    assert err.strip().splitlines()[0] == message


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_manifest_path_leaving_a_pinned_tree_exits_2_as_for_an_unpinned_one(name, tmp_path, monkeypatch, capsys):
    repo, sha = _repo_with_sub(tmp_path)
    code, err = _run(monkeypatch, capsys, *_command(name, f"{repo}@{sha}", tmp_path), "--manifest", "../outside.yaml")
    assert code == 2
    assert err.strip().splitlines()[0] == "Error: path escapes the repository: ../outside.yaml"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_tree_refuses_a_path_that_leaves_the_tree_and_keeps_one_that_stays(tmp_path):
    from dokimi.repos.source import GitTree, SourceArgumentError

    repo, sha = _repo_with_sub(tmp_path)
    tree = GitTree(repo / ".git", sha)
    for probe in (tree.is_file, tree.is_dir, tree.read_text, tree.read_bytes, tree.resolve):
        for path in ("../x", "sub/../../x", "/sub/f.txt"):
            with pytest.raises(SourceArgumentError, match=f"^path escapes the repository: {path}$"):
                probe(path)
    assert tree.is_file("sub/../sub/f.txt")
    assert tree.is_dir("sub/./")
    assert not tree.is_file("sub/missing.txt")


def _tree_with_a_link_out(tmp_path, link):
    """A git work tree whose ``link`` points at a manifest outside it."""
    tree = tmp_path / "tree"
    (tree / link).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "outside.yaml").write_text("schema: repo_manifest.v1\n", encoding="utf-8")
    try:
        os.symlink(os.path.relpath(tmp_path / "outside.yaml", (tree / link).parent), tree / link)
    except OSError:
        pytest.skip("this file system cannot hold a symlink")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "l"]):
        subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    return tree, sha


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
@pytest.mark.parametrize("pinned", [False, True])
def test_a_default_manifest_linking_out_of_the_tree_is_a_bad_manifest_with_exit_1(name, pinned, tmp_path,
                                                                                  monkeypatch, capsys):
    tree, sha = _tree_with_a_link_out(tmp_path, ".agent-eval/repo-eval.yaml")
    code, err = _run(monkeypatch, capsys, *_command(name, f"{tree}@{sha}" if pinned else str(tree), tmp_path))
    assert code == 1
    assert err.strip().splitlines()[0].startswith("✗ path escapes the repository: .agent-eval/repo-eval.yaml")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_a_typed_manifest_inside_the_tree_that_links_out_of_it_exits_1(name, tmp_path, monkeypatch, capsys):
    tree, _ = _tree_with_a_link_out(tmp_path, "escape.yaml")
    code, err = _run(monkeypatch, capsys, *_command(name, str(tree), tmp_path), "--manifest", "escape.yaml")
    assert code == 1
    assert err.strip().splitlines()[0] == "✗ path escapes the repository: escape.yaml"


@pytest.mark.skipif(shutil.which("git") is None or sys.platform in ("win32", "darwin"),
                    reason="needs git and a file system that takes a name that is not UTF-8")
@pytest.mark.parametrize("name", ["check", "run", "replay", "prs"])
def test_a_pinned_subdirectory_whose_name_is_not_utf8_exits_1_without_a_traceback(name, tmp_path, monkeypatch,
                                                                                   capsys):
    repo, sha = _repo_with_sub(tmp_path)
    sub = os.fsdecode(os.fsencode(str(repo)) + b"/s\xff")
    os.mkdir(sub)
    code, err = _run(monkeypatch, capsys, *_command(name, f"{sub}@{sha}", tmp_path))
    assert code == 1
    assert err.strip().startswith("✗ ")


@pytest.mark.parametrize("name", ["check", "run"])
def test_check_and_run_keep_exit_1_for_a_pinned_source_that_cannot_be_fetched(name, tmp_path, monkeypatch, capsys):
    from dokimi.repos import SourceError

    def failed(source, cache_dir=None):
        raise SourceError("git fetch failed: unreachable")

    monkeypatch.setattr("dokimi.repos.open_source", failed)
    code, err = _run(monkeypatch, capsys, *_command(name, f"https://127.0.0.1:9/r.git@{PIN}", tmp_path))
    assert code == 1
    assert err.strip() == "✗ git fetch failed: unreachable"


def test_a_windows_manifest_path_leaving_an_unpinned_tree_is_refused_as_typed(tmp_path, monkeypatch):
    import ntpath
    from types import SimpleNamespace

    from dokimi.commands import repo_run
    from dokimi.utils import InputError

    source = SimpleNamespace(location=str(tmp_path), remote=False, rev=None, subdir=None)
    # What Windows does with `..\x`, written back with `/` so the posix tree compares.
    windows_path = SimpleNamespace(normpath=lambda p: ntpath.normpath(p).replace("\\", "/"), sep="/",
                                  splitdrive=ntpath.splitdrive)
    monkeypatch.setattr(repo_run, "os", SimpleNamespace(path=windows_path))  # only this module; posixpath stays
    for manifest in ("..\\outside.yaml", "sub\\..\\..\\outside.yaml"):
        with pytest.raises(InputError, match=r"^path escapes the repository: "):
            repo_run._refuse_typed(source, SimpleNamespace(manifest=manifest, manifest_file=None))
    repo_run._refuse_typed(source, SimpleNamespace(manifest="sub\\..\\ok.yaml", manifest_file=None))


def test_a_windows_drive_manifest_is_refused_for_an_unpinned_tree(tmp_path, monkeypatch):
    import ntpath
    from types import SimpleNamespace

    from dokimi.commands import repo_run
    from dokimi.utils import InputError

    source = SimpleNamespace(location=str(tmp_path), remote=False, rev=None, subdir=None)
    windows_path = SimpleNamespace(normpath=ntpath.normpath, sep="\\", splitdrive=ntpath.splitdrive)
    monkeypatch.setattr(repo_run, "os", SimpleNamespace(path=windows_path))
    # `D:/other/x.yaml` is not absolute for the `/` check on the tree path: only the drive check refuses it.
    with pytest.raises(InputError) as raised:
        repo_run._refuse_typed(source, SimpleNamespace(manifest="D:\\other\\x.yaml", manifest_file=None))
    assert str(raised.value) == "path escapes the repository: D:\\other\\x.yaml"


def test_a_windows_manifest_path_leaving_a_pinned_tree_is_refused_as_typed(monkeypatch):
    import ntpath
    from types import SimpleNamespace

    from dokimi.commands import repo_run
    from dokimi.utils import InputError

    pinned = SimpleNamespace(location="https://example.invalid/r.git", remote=True, rev="a" * 40, subdir=None)
    windows_path = SimpleNamespace(normpath=ntpath.normpath, sep="\\", splitdrive=ntpath.splitdrive)  # as Windows
    monkeypatch.setattr(repo_run, "os", SimpleNamespace(path=windows_path))
    for manifest in ("..\\outside.yaml", "sub\\..\\..\\outside.yaml", "D:/other/x.yaml", "D:\\other\\x.yaml",
                     "D:x.yaml", "//host/share/x.yaml"):
        with pytest.raises(InputError) as raised:
            repo_run._refuse_typed(pinned, SimpleNamespace(manifest=manifest, manifest_file=None))
        assert str(raised.value) == f"path escapes the repository: {manifest}"
    repo_run._refuse_typed(pinned, SimpleNamespace(manifest="sub\\..\\ok.yaml", manifest_file=None))


def test_a_pinned_source_gets_only_the_tree_path_check_for_a_manifest_that_stays_in_the_tree(tmp_path):
    from types import SimpleNamespace

    from dokimi.commands import repo_run
    from dokimi.utils import InputError

    pinned = SimpleNamespace(location="https://example.invalid/r.git", remote=True, rev="a" * 40, subdir=None)
    repo_run._refuse_typed(pinned, SimpleNamespace(manifest="sub/../ok.yaml", manifest_file=None))
    with pytest.raises(InputError, match=r"^path escapes the repository: \.\./x\.yaml$"):
        repo_run._refuse_typed(pinned, SimpleNamespace(manifest="../x.yaml", manifest_file=None))
    unpinned = SimpleNamespace(location=str(tmp_path), remote=False, rev=None, subdir=None)
    repo_run._refuse_typed(unpinned, SimpleNamespace(manifest="sub/../ok.yaml", manifest_file=None))


def test_no_typed_manifest_or_a_manifest_file_leaves_the_manifest_path_unchecked(tmp_path):
    from types import SimpleNamespace

    from dokimi.commands import repo_run

    source = SimpleNamespace(location=str(tmp_path), remote=False, rev=None, subdir=None)
    assert repo_run._refuse_typed(source, SimpleNamespace(manifest=None, manifest_file=None)) is None
    # `--manifest-file` supplies the manifest, so a `--manifest` beside it is not read as a tree path.
    assert repo_run._refuse_typed(source, SimpleNamespace(manifest="../x.yaml", manifest_file="m.yaml")) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_tree_follows_a_link_before_it_goes_up_a_level(tmp_path):
    from dokimi.repos.source import GitTree, SourceArgumentError, SourceError

    tree = tmp_path / "t"
    (tree / "sub" / "deep").mkdir(parents=True)
    (tree / "sub" / "deep" / "f.txt").write_text("deep", encoding="utf-8")  # git holds no empty directory
    (tree / "repo-eval.yaml").write_text("root", encoding="utf-8")
    (tree / "sub" / "repo-eval.yaml").write_text("sub", encoding="utf-8")
    try:
        os.symlink("sub/deep", tree / "link")
    except OSError:
        pytest.skip("this file system cannot hold a symlink")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "l"]):
        subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    git_tree = GitTree(tree / ".git", sha)
    assert git_tree.read_text("link/../repo-eval.yaml") == "sub"
    assert git_tree.read_bytes("link/../repo-eval.yaml") == b"sub"
    assert git_tree.resolve("link/../repo-eval.yaml") == "sub/repo-eval.yaml"
    assert git_tree.read_many(["link/../repo-eval.yaml"]) == {"link/../repo-eval.yaml": "sub"}
    assert git_tree.is_file("link/../repo-eval.yaml")
    assert git_tree.resolve("link/../../repo-eval.yaml") == "repo-eval.yaml"
    with pytest.raises(SourceArgumentError, match="^path escapes the repository: link/../../../repo-eval.yaml$"):
        git_tree.resolve("link/../../../repo-eval.yaml")
    # A `..` after a directory the tree lacks is no path, as ENOENT is in a checkout; a file is no directory either.
    for missing in ("nope/../repo-eval.yaml", "repo-eval.yaml/../repo-eval.yaml"):
        assert not git_tree.is_file(missing)
        assert not git_tree.is_dir(missing)
        with pytest.raises(SourceError, match=f"^no such path in {sha[:12]}: {missing}$"):
            git_tree.read_text(missing)
    assert not git_tree.is_dir("nope/..")
    assert git_tree.is_dir("sub/..")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_tree_reads_a_dot_dot_after_a_gitlink_as_a_checkout_does(tmp_path):
    from dokimi.repos.source import GitTree, SourceArgumentError, SourceError

    tree = tmp_path / "t"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "x.txt").write_text("x", encoding="utf-8")
    commit = "1" * 40
    git = ["git", "-C", str(tree)]
    for args in (["init", "-q"], ["add", "-A"],
                 ["update-index", "--add", "--cacheinfo", f"160000,{commit},sub/mod"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "g"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    git_tree = GitTree(tree / ".git", sha)
    assert git_tree.is_file("sub/mod/../x.txt")
    assert git_tree.read_text("sub/mod/../x.txt") == "x"
    assert git_tree.resolve("sub/mod/../x.txt") == "sub/x.txt"
    assert git_tree.is_dir("sub/mod")
    assert not git_tree.is_file("sub/mod")
    assert git_tree.files() == ["sub/x.txt"]  # a checkout holds a directory at the gitlink, not a file
    with pytest.raises(SourceArgumentError, match="^sub/mod is a directory$"):
        git_tree.read_text("sub/mod")
    assert git_tree.read_many(["sub/mod"]) == {}
    # A `..` after a path below the gitlink, or after one the tree lacks, is still no path.
    for missing in ("sub/mod/y/../x.txt", "sub/nope/../x.txt"):
        assert not git_tree.is_file(missing)
        with pytest.raises(SourceError, match=f"^no such path in {sha[:12]}: {missing}$"):
            git_tree.read_text(missing)
