# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` on a git working tree with a tracked file under a directory that cannot be searched (#959)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                                reason="root ignores directory modes")


def _lock(path):
    """Make ``path`` unsearchable; its mode before, to restore."""
    mode = path.stat().st_mode & 0o777
    path.chmod(0o000)
    return mode


def _repo(path):
    git = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)  # noqa: E731
    (path / "d").mkdir()
    (path / "d" / "f.txt").write_text("hi\n", encoding="utf-8")
    (path / "AGENTS.md").write_text("x\n", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")


def test_a_tracked_file_under_an_unsearchable_directory_is_skipped_and_named(tmp_path, capsys):
    _repo(tmp_path)
    mode = _lock(tmp_path / "d")
    try:
        tree = WorkTree(tmp_path)
        assert tree.files() == ["AGENTS.md"]
        assert {e["path"] for e in budget(tree, NAMES)["entries"]} == {"AGENTS.md"}
    finally:
        (tmp_path / "d").chmod(mode)
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]
    assert capsys.readouterr().err == ""


def test_a_searchable_directory_lists_its_files_without_a_warning(tmp_path):
    _repo(tmp_path)
    tree = WorkTree(tmp_path)
    assert tree.files() == ["AGENTS.md", "d/f.txt"]
    assert tree.skipped == []


def test_the_warning_does_not_depend_on_pathlib_raising(tmp_path, capsys, monkeypatch):
    """#959: Python 3.14's `Path.is_file` and `Path.is_symlink` return False on PermissionError instead of
    raising; the path must still be named in `skipped`."""
    def quiet(original):
        def check(self):
            try:
                return original(self)
            except PermissionError:
                return False
        return check

    _repo(tmp_path)
    monkeypatch.setattr(Path, "is_file", quiet(Path.is_file))
    monkeypatch.setattr(Path, "is_symlink", quiet(Path.is_symlink))
    mode = _lock(tmp_path / "d")
    try:
        tree = WorkTree(tmp_path)
        assert tree.files() == ["AGENTS.md"]
    finally:
        (tmp_path / "d").chmod(mode)
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]


def test_a_link_and_a_deleted_file_in_the_listing(tmp_path):
    _repo(tmp_path)
    (tmp_path / "link").symlink_to("nowhere")
    (tmp_path / "d" / "f.txt").unlink()
    subprocess.run(["git", "-C", str(tmp_path), "add", "link"], check=True, capture_output=True)
    tree = WorkTree(tmp_path)
    assert tree.files() == ["AGENTS.md", "link"]  # a dangling link stays, a deleted file goes
    assert tree.skipped == []


def test_the_skipped_path_is_a_budget_warning_that_fails_the_gate(tmp_path, capsys):
    """#959; schemas/repo_budget.v1.json: `warnings` is what `--fail-on warn` gates on, unreadable files included."""
    _repo(tmp_path)
    mode = _lock(tmp_path / "d")
    try:
        assert budget(WorkTree(tmp_path), NAMES)["warnings"] == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]
        args = build_parser().parse_args(["repo", "budget", str(tmp_path), "--fail-on", "warn"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(mode)
    assert exc.value.code == 1
    assert "warning: d/f.txt: Permission denied; the file and anything it leads to are not counted" in capsys.readouterr().out


def test_a_skipped_guidance_file_makes_repo_scan_incomplete(tmp_path, capsys):
    """#959; `repo scan --fail-on fail` exits 1 on an incomplete scan: a guidance file it could not list is not checked."""
    _repo(tmp_path)
    (tmp_path / "d" / "AGENTS.md").write_text("Read `missing.py`.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on", "fail", "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(mode)
    assert exc.value.code == 1
    assert "d/AGENTS.md: Permission denied" in capsys.readouterr().err


def test_a_skipped_guidance_file_makes_a_written_manifest_scan_incomplete(tmp_path, capsys):
    """#959; the same gate with `.agent-eval/repo-eval.yaml`, which does not go through the budget warnings."""
    _repo(tmp_path)
    (tmp_path / "d" / "AGENTS.md").write_text("Read `missing.py`.\n", encoding="utf-8")
    (tmp_path / ".agent-eval").mkdir()
    (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text(
        '{"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["**/AGENTS.md"]}}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on", "fail", "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(mode)
    assert exc.value.code == 1
    assert "d/AGENTS.md: Permission denied" in capsys.readouterr().err


@pytest.mark.parametrize("written", [False, True])
def test_a_skipped_data_file_does_not_make_repo_scan_incomplete(tmp_path, written):
    """#959 review of #1028: only a skipped path the scan would check is a reason; `d/f.txt` is not guidance."""
    _repo(tmp_path)
    if written:
        (tmp_path / ".agent-eval").mkdir()
        (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text(
            '{"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["**/AGENTS.md"]}}\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on", "fail", "--json"])
        try:
            args.func(args)
            code: object = 0
        except SystemExit as exc:
            code = exc.code
    finally:
        (tmp_path / "d").chmod(mode)
    assert code in (0, None)


def _scan_exit(tmp_path, *extra):
    args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on", "fail", "--json", *extra])
    try:
        args.func(args)
    except SystemExit as exc:
        return exc.code
    return 0


def _track(tmp_path, files):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)


def _manifest(tmp_path, body):
    _track(tmp_path, {".agent-eval/repo-eval.yaml": '{"schema": "repo_manifest.v1", "id": "toy", ' + body + "}\n"})


def test_a_skipped_claude_local_md_makes_the_default_scan_incomplete(tmp_path):
    """#959 review of #1028: `CLAUDE.local.md` is guidance by the profile's names, though no default glob matches it."""
    _repo(tmp_path)
    _track(tmp_path, {"d/CLAUDE.local.md": "Read `missing.py`.\n"})
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def test_a_skipped_path_matching_an_also_scan_glob_makes_the_scan_incomplete(tmp_path):
    """#959 review of #1028: `also_scan` entries are globs, as `guidance_files` reads them."""
    _repo(tmp_path)
    _track(tmp_path, {"docs/a.md": "x\n", "docs/private/b.md": "Read `missing.py`.\n"})
    _manifest(tmp_path, '"guidance": {"globs": ["AGENTS.md"], "also_scan": ["docs/**/*.md"]}')
    mode = _lock(tmp_path / "docs" / "private")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "docs" / "private").chmod(mode)


@pytest.mark.parametrize("written", [False, True])
def test_a_skipped_bazel_build_input_makes_the_scan_incomplete(tmp_path, written):
    """#959 review of #1028: a `.bzl` file the scan cannot list is a build input it cannot read."""
    _repo(tmp_path)
    _track(tmp_path, {"MODULE.bazel": "module(name = \"m\")\n", "BUILD": "x\n", "d/macros.bzl": "def f():\n    pass\n", "AGENTS.md": "Run `bazel build //...`.\n"})
    if written:
        _manifest(tmp_path, '"guidance": {"globs": ["AGENTS.md"]}')
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


@pytest.mark.parametrize("written", [False, True])
def test_a_skipped_path_with_a_colon_and_a_space_is_matched_by_its_whole_name(tmp_path, written):
    """#959 review of #1028: `team: docs/AGENTS.md` is one path; the warning text is not split to find it."""
    _repo(tmp_path)
    _track(tmp_path, {"team: docs/AGENTS.md": "Read `missing.py`.\n"})
    if written:
        _manifest(tmp_path, '"guidance": {"globs": ["**/AGENTS.md"]}')
    mode = _lock(tmp_path / "team: docs")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "team: docs").chmod(mode)


def test_the_warning_names_the_reason_without_the_locale(tmp_path, monkeypatch):
    """#959 review of #1028: `strerror` follows the locale; the warning says `Permission denied` whatever it says."""
    real = os.lstat

    def lstat(path, *a, **k):
        if str(path).endswith("f.txt"):
            raise PermissionError(13, "Zugriff verweigert")
        return real(path, *a, **k)

    _repo(tmp_path)
    monkeypatch.setattr(os, "lstat", lstat)
    tree = WorkTree(tmp_path)
    tree.files()
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]


def test_a_skipped_markdown_import_of_the_default_guidance_makes_the_scan_incomplete(tmp_path):
    """#959 review of #1028: `@d/context.md` in CLAUDE.md is loaded by default, so `d/context.md` is checked."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@d/context.md\n", "d/context.md": "Read `missing.py`.\n"})
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def test_a_skipped_bazel_build_input_without_the_bazel_adapter_is_only_a_warning(tmp_path):
    """#959 review of #1028: `scan_repo` reads build inputs only with the Bazel adapter; `adapters.detect` looks at root markers."""
    _repo(tmp_path)
    _track(tmp_path, {"Makefile": "all:\n\ttrue\n", "vendor/lib/BUILD": "x\n"})
    mode = _lock(tmp_path / "vendor" / "lib")
    try:
        assert _scan_exit(tmp_path) == 0
    finally:
        (tmp_path / "vendor" / "lib").chmod(mode)


def test_a_plain_directory_names_an_unsearchable_directory_and_fails_the_gate(tmp_path):
    """#959 review of #1028: outside git the `os.walk` fallback records the directory, so the gate does not depend on `.git`."""
    (tmp_path / "AGENTS.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "AGENTS.md").write_text("Read `missing.py`.\n", encoding="utf-8")
    mode = _lock(tmp_path / "d")
    try:
        tree = WorkTree(tmp_path)
        assert tree.files() == ["AGENTS.md"]
        assert tree.skipped == ["d: Permission denied; the file and anything it leads to are not counted"]
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def test_an_unmerged_path_is_named_once(tmp_path):
    """#959 review of #1028: `ls-files --cached` lists an unmerged path once per stage."""
    _repo(tmp_path)
    sha = subprocess.run(["git", "-C", str(tmp_path), "hash-object", "-w", "d/f.txt"], check=True,
                         capture_output=True, text=True).stdout.strip()
    index = "".join(f"100644 {sha} {n}\td/f.txt\n" for n in (1, 2, 3))
    subprocess.run(["git", "-C", str(tmp_path), "update-index", "--index-info"], input="0 " + "0" * 40 + "\td/f.txt\n" + index,
                   check=True, capture_output=True, text=True)
    mode = _lock(tmp_path / "d")
    try:
        tree = WorkTree(tmp_path)
        tree.files()
    finally:
        (tmp_path / "d").chmod(mode)
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]


def test_a_workspace_export_refuses_a_tree_with_an_unsearchable_directory(tmp_path):
    """#959 review of #1028: `_export` copies `tree.files()`; a workspace that lacks `d/f.txt` must not be built."""
    from dokimi.repos.errors import PrepError
    from dokimi.repos.workspace import _export

    src = tmp_path / "src"
    src.mkdir()
    _repo(src)
    mode = _lock(src / "d")
    try:
        with pytest.raises(PrepError, match=r"d/f\.txt: Permission denied"):
            _export(WorkTree(src), tmp_path / "ws", None, None)
    finally:
        (src / "d").chmod(mode)
    assert not (tmp_path / "ws").exists()


@pytest.mark.parametrize("only, exclude", [("eval", None), (None, "private")])
def test_a_workspace_export_ignores_a_skipped_path_outside_what_it_copies(tmp_path, only, exclude):
    """#959 review of #1028: `private/data.txt` is not in `eval` and is in the excluded directory."""
    from dokimi.repos.workspace import _export

    src = tmp_path / "src"
    src.mkdir()
    _repo(src)
    (src / "eval").mkdir()
    (src / "eval" / "task.json").write_text("{}\n", encoding="utf-8")
    (src / "private").mkdir()
    (src / "private" / "data.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, capture_output=True)
    mode = _lock(src / "private")
    try:
        _export(WorkTree(src), tmp_path / "ws", only, exclude)
    finally:
        (src / "private").chmod(mode)
    assert (tmp_path / "ws" / ("task.json" if only else "eval/task.json")).is_file()


def test_a_skipped_markdown_import_through_a_linked_directory_makes_the_scan_incomplete(tmp_path):
    """#959 review of #1028: `@lnk/context.md` with `lnk -> d` names the link's path, not `d/context.md`."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@lnk/context.md\n", "d/context.md": "Read `missing.py`.\n"})
    os.symlink("d", tmp_path / "lnk")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def test_a_skipped_markdown_import_through_a_file_link_makes_the_scan_incomplete(tmp_path):
    """#959 review of #1028: `@alias.md` with `alias.md -> d/context.md` and `d` unsearchable."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@alias.md\n", "d/context.md": "Read `missing.py`.\n"})
    os.symlink("d/context.md", tmp_path / "alias.md")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def test_a_skipped_markdown_import_through_a_link_to_the_root_makes_the_scan_incomplete(tmp_path):
    """#959 review of #1028: `@self/d/context.md` with `self -> .` and `d` unsearchable."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@self/d/context.md\n", "d/context.md": "Read `missing.py`.\n"})
    os.symlink(".", tmp_path / "self")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "d").chmod(mode)


def _scan_through_links(tmp_path, links):
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@alias.md\n", "d/context.md": "Read `missing.py`.\n"})
    for name, target in links.items():
        os.symlink(target, tmp_path / name)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "d")
    try:
        return _scan_exit(tmp_path)
    finally:
        (tmp_path / "d").chmod(mode)


def test_a_skipped_markdown_import_through_an_absolute_file_link_makes_the_scan_incomplete(tmp_path):
    """Second review: `alias.md` links to the absolute path of `d/context.md`, inside the repository."""
    assert _scan_through_links(tmp_path, {"alias.md": str(tmp_path / "d/context.md")}) == 1


def test_a_skipped_markdown_import_through_an_absolute_link_with_dot_components_makes_the_scan_incomplete(tmp_path):
    """Review of #1028: `alias.md -> <parent>/./<repo>/d/context.md` is the same file as the canonical path."""
    target = f"{tmp_path.parent}/./{tmp_path.name}//d/./context.md"
    assert _scan_through_links(tmp_path, {"alias.md": target}) == 1


@pytest.mark.parametrize("backslashes", ["relative", "absolute", "extended"])
def test_a_skipped_markdown_import_through_a_link_with_native_windows_separators_makes_the_scan_incomplete(
        tmp_path, monkeypatch, backslashes):
    """Review of #1028: `os.readlink` gives `d\\context.md` on Windows; the tree lists `d/context.md`."""
    from dokimi.commands import repo_scan

    monkeypatch.setattr(repo_scan, "_WINDOWS", True)
    absolute = str(tmp_path / "d/context.md").replace("/", "\\")
    target = {"relative": "d\\context.md", "absolute": absolute, "extended": "\\\\?\\" + absolute}[backslashes]
    assert _scan_through_links(tmp_path, {"alias.md": target}) == 1


def test_a_skipped_markdown_import_through_a_relative_link_under_a_drive_named_directory_makes_the_scan_incomplete(
        tmp_path):
    """Review of #1028: off Windows `C:/d/context.md` is a relative target under a directory named `C:`."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@alias.md\n", "C:/d/context.md": "Read `missing.py`.\n"})
    os.symlink("C:/d/context.md", tmp_path / "alias.md")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "C:" / "d")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "C:" / "d").chmod(mode)


def test_a_skipped_markdown_import_through_a_chain_of_file_links_makes_the_scan_incomplete(tmp_path):
    """Second review: `alias.md -> second.md -> d/context.md`."""
    assert _scan_through_links(tmp_path, {"alias.md": "second.md", "second.md": "d/context.md"}) == 1


def test_a_skipped_markdown_import_through_a_link_into_a_dot_dot_named_directory_makes_the_scan_incomplete(tmp_path):
    """Second review: `alias.md -> ..docs/context.md`; `..docs` is a name, not a parent directory."""
    _repo(tmp_path)
    _track(tmp_path, {"CLAUDE.md": "@alias.md\n", "..docs/context.md": "Read `missing.py`.\n"})
    os.symlink("..docs/context.md", tmp_path / "alias.md")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    mode = _lock(tmp_path / "..docs")
    try:
        assert _scan_exit(tmp_path) == 1
    finally:
        (tmp_path / "..docs").chmod(mode)


def test_a_skipped_markdown_import_through_a_file_link_over_a_directory_link_makes_the_scan_incomplete(tmp_path):
    """Second review: `alias.md -> linked/context.md` with `linked -> d` and `d` unsearchable."""
    assert _scan_through_links(tmp_path, {"alias.md": "linked/context.md", "linked": "d"}) == 1


@pytest.mark.parametrize("written", [False, True])
def test_a_skipped_data_file_stays_a_warning_of_the_scan_report(tmp_path, capsys, written):
    """#959 review of #1028: `d/f.txt` is not a reason to fail, but the report names it in `warnings`."""
    import json

    _repo(tmp_path)
    if written:
        _manifest(tmp_path, '"guidance": {"globs": ["**/AGENTS.md"]}')
    mode = _lock(tmp_path / "d")
    try:
        assert _scan_exit(tmp_path) == 0
    finally:
        (tmp_path / "d").chmod(mode)
    payload = json.loads(capsys.readouterr().out)["data"]
    assert any(w.startswith("d/f.txt: Permission denied") for w in payload["warnings"])
    assert not payload["incomplete"]
