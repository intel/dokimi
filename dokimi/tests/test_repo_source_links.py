# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1084: a committed link gets one verdict from a directory and from ``<dir>@HEAD``.

``GitTree`` refuses a link whose ``..`` goes above the root; ``WorkTree`` did so only when the resolved path
left the directory, so a link that came back in by the directory's own name
was read from the directory and refused from the revision.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi.repos import links, source
from dokimi.repos.source import GitTree, SourceArgumentError, SourceError, WorkTree


def _repo(tmp_path: Path, links: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "real.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "sub" / "f.txt").write_text("f\n", encoding="utf-8")
    for name, text in links.items():
        try:
            os.symlink(text.replace("<root>", str(root)), root / name)
        except OSError:
            pytest.skip("this file system cannot hold a symlink")
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "a@b.c")
    git("config", "user.name", "a")
    git("add", "-A")
    git("commit", "-qm", "c")
    return root


def _trees(root: Path):
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    return [WorkTree(root), GitTree(root / ".git", sha)]


@pytest.mark.parametrize("text", ["../repo/real.yaml", "<out>/x", "../../x/../repo/real.yaml"])
def test_a_link_that_leaves_and_returns_escapes_in_both_trees(tmp_path, text):
    (tmp_path / "x").write_text("x\n", encoding="utf-8")
    text = text.replace("<out>", str(tmp_path))
    absolute = os.path.isabs(text)
    root = _repo(tmp_path, {"link.yaml": text} if absolute else {})
    if not absolute:
        (root / "sub" / "rt.yaml").symlink_to("../" + text)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "d"], cwd=root, check=True, capture_output=True)
    path = "link.yaml" if absolute else "sub/rt.yaml"
    # GitTree reads a committed link text as POSIX, so ``C:\...`` there is not absolute
    for tree in _trees(root) if text.startswith("/") or not absolute else [WorkTree(root)]:
        with pytest.raises(SourceArgumentError, match="path escapes the repository"):
            tree.resolve(path)


def test_an_absolute_link_into_the_directory_is_followed_by_the_directory(tmp_path):
    root = _repo(tmp_path, {"abs.yaml": "<root>/real.yaml"})
    assert WorkTree(root).resolve("abs.yaml") == "real.yaml"


def test_a_link_that_stays_inside_resolves_alike_in_both_trees(tmp_path):
    root = _repo(tmp_path, {"in.yaml": "real.yaml", "sub/up.yaml": "../real.yaml", "dirlink": "sub"})
    for tree in _trees(root):
        assert tree.resolve("in.yaml") == "real.yaml"
        assert tree.resolve("sub/up.yaml") == "real.yaml"
        assert tree.resolve("dirlink/f.txt") == "sub/f.txt"
        assert tree.resolve("dirlink/../real.yaml") == "real.yaml"


def test_a_dotdot_above_the_root_escapes_in_both_trees(tmp_path):
    root = _repo(tmp_path, {})
    for tree in _trees(root):
        with pytest.raises(SourceArgumentError, match="path escapes the repository"):
            tree.resolve("../repo/real.yaml")


def test_a_link_loop_is_refused_in_both_trees(tmp_path):
    """#1084: a link loop gets the same refusal from a directory and from a revision."""
    root = _repo(tmp_path, {"a": "b", "b": "a"})
    for tree in _trees(root):
        with pytest.raises(SourceArgumentError, match="too many levels|links to nothing"):
            tree.resolve("a")


def test_an_absolute_path_inside_the_directory_is_still_read(tmp_path):
    root = _repo(tmp_path, {})
    assert WorkTree(root).read_text(str(root / "real.yaml")) == "version: 1\n"


def test_an_absolute_path_to_a_link_that_leaves_and_returns_escapes(tmp_path):
    """#1084: an absolute input path is walked by its links too, not only checked where it lands."""
    root = _repo(tmp_path, {"link.yaml": "../repo/real.yaml"})
    with pytest.raises(SourceArgumentError, match="path escapes the repository"):
        WorkTree(root).read_text(str(root / "link.yaml"))


def test_a_chain_of_more_than_forty_links_still_refuses_a_link_that_leaves_and_returns(tmp_path):
    """#1084: a chain past the 40-link limit is refused, not resolved by where it lands."""
    links = {f"a{i}": f"a{i + 1}" for i in range(1, 41)} | {"a41": "../repo/real.yaml"}
    root = _repo(tmp_path, links)
    with pytest.raises(SourceArgumentError, match="path escapes the repository|too many levels"):
        WorkTree(root).read_text("a1")


def test_an_absolute_link_to_a_link_that_leaves_and_returns_escapes(tmp_path):
    """#1084: an absolute link does not hide a leave-and-return link farther along the chain."""
    root = _repo(tmp_path, {"inner.yaml": "../repo/real.yaml", "outer.yaml": "<root>/inner.yaml"})
    with pytest.raises(SourceArgumentError, match="path escapes the repository"):
        WorkTree(root).resolve("outer.yaml")


def test_an_absolute_path_through_a_link_goes_up_from_the_links_target(tmp_path):
    """#1084: ``link/..`` in an absolute path or link text ascends from the target of ``link``, as in a checkout."""
    root = _repo(tmp_path, {"link": "sub/deep", "outer.txt": "<root>/link/../f.txt"})
    (root / "sub" / "deep").mkdir()
    (root / "f.txt").write_text("root", encoding="utf-8")
    (root / "sub" / "f.txt").write_text("sub", encoding="utf-8")
    assert WorkTree(root).read_text(str(root / "link" / ".." / "f.txt")) == "sub"
    # Windows stores an absolute link text with its ``..`` already collapsed: the OS reads ``root/f.txt`` too
    assert WorkTree(root).read_text("outer.txt") == ("root" if os.name == "nt" else "sub")


@pytest.mark.skipif(os.sep == "\\", reason="a backslash separates components on Windows")
def test_a_backslash_in_a_link_text_is_a_filename_character(tmp_path):
    """#1084: on POSIX a backslash in a link's text is part of a name, so the link reads the file named so."""
    root = _repo(tmp_path, {"link": "a\\b.txt"})
    (root / "a").mkdir()
    (root / "a" / "b.txt").write_text("nested", encoding="utf-8")
    (root / "a\\b.txt").write_text("literal", encoding="utf-8")
    assert WorkTree(root).read_text("link") == "literal"


def test_a_checkout_alias_does_not_hide_a_leave_and_return_link(tmp_path):
    """#1084: ``alias -> repo`` is resolved without consuming the link text after it."""
    root = _repo(tmp_path, {"inner.yaml": "../repo/real.yaml"})
    os.symlink(root, tmp_path / "alias")
    os.symlink(str(tmp_path / "alias" / "inner.yaml"), root / "outer.yaml")
    tree = WorkTree(root)
    for path in (str(tmp_path / "alias" / "inner.yaml"), "outer.yaml"):
        with pytest.raises(SourceArgumentError, match="path escapes the repository"):
            tree.resolve(path)
    assert tree.read_text(str(tmp_path / "alias" / "real.yaml")) == "version: 1\n"


def test_an_alias_to_a_link_inside_the_directory_does_not_hide_a_leave_and_return_link(tmp_path):
    """#1084: ``alias -> repo/esc`` is walked through ``esc``, not resolved past it."""
    root = _repo(tmp_path, {"esc": "../repo/sub"})
    os.symlink(root / "esc", tmp_path / "alias")
    os.symlink(str(tmp_path / "alias" / "f.txt"), root / "outer.yaml")
    tree = WorkTree(root)
    for path in (str(tmp_path / "alias" / "f.txt"), "outer.yaml", "esc/f.txt"):
        with pytest.raises(SourceArgumentError, match="path escapes the repository"):
            tree.resolve(path)


def test_a_junction_out_of_the_tree_escapes(tmp_path, monkeypatch):
    """A Windows junction is not a symlink: it is followed by its target, which must lie under the root."""
    root = _repo(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    (root / "external").mkdir()
    real_readlink, real_lstat = os.readlink, os.lstat
    junction = lambda p: SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=0xA0000003)  # noqa: E731
    monkeypatch.setattr(os, "lstat", lambda p: junction(p) if Path(p).name == "external" else real_lstat(p))
    monkeypatch.setattr(os, "readlink", lambda p: str(outside) if Path(p).name == "external" else real_readlink(p))
    with pytest.raises(SourceArgumentError, match="path escapes the repository"):
        WorkTree(root).read_text("external/secret.txt")


@pytest.mark.skipif(os.name != "nt", reason="a drive or a leading separator in a relative path is Windows only")
def test_a_path_with_another_drive_or_a_leading_separator_does_not_leave_the_directory(tmp_path):
    """``Q:x`` reads drive Q's current directory and ``\\x`` the current drive's root, not the directory."""
    root = _repo(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    free = [d for d in "QRSTUVWXYZ" if not Path(f"{d}:\\").exists()]
    if not free or subprocess.run(["subst", f"{free[0]}:", str(outside)], capture_output=True).returncode:
        pytest.skip("no drive letter to subst")
    try:
        for path in (f"{free[0]}:secret.txt", f"sub/{free[0]}:secret.txt", str(outside / "secret.txt")[2:]):
            with pytest.raises(SourceArgumentError, match="path escapes the repository"):
                WorkTree(root).read_text(path)
    finally:
        subprocess.run(["subst", f"{free[0]}:", "/d"], capture_output=True)


@pytest.mark.skipif(os.name != "nt", reason="a junction is Windows only")
def test_a_real_junction_out_of_the_tree_escapes(tmp_path):
    """A junction made by Windows itself, with no API stood in: Python 3.11 has no ``Path.is_junction``."""
    import _winapi

    root = _repo(tmp_path, {})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "external"))  # type: ignore[attr-defined]  # Windows only
    with pytest.raises(SourceArgumentError, match="path escapes the repository"):
        WorkTree(root).read_text("external/secret.txt")


def test_an_extended_unc_link_text_is_the_share(monkeypatch):
    """``\\\\?\\UNC\\server\\share\\x`` is the share ``\\\\server\\share\\x``; cut by four characters it is the relative ``UNC\\server``."""
    link = Path("link")  # made before ``os.name`` changes: Python 3.11 cannot build a WindowsPath on POSIX
    with monkeypatch.context() as m:
        m.setattr(os, "readlink", lambda p: "\\\\?\\UNC\\server\\share\\repo\\real.yaml")
        m.setattr(os, "name", "nt")
        text = links._link_target(link)
    assert text == "\\\\server\\share\\repo\\real.yaml"


def test_an_alias_to_the_checkout_root_itself_resolves_to_dot(tmp_path, monkeypatch):
    """A link whose target is only an anchor (``\\\\server\\share\\`` as the checkout root) leaves nothing to walk: it is the root."""
    root = _repo(tmp_path, {})
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    start = links._start
    with monkeypatch.context() as m:
        m.setattr(links, "_link_target", lambda p: "ROOT")
        m.setattr(links, "_start", lambda text, here: (root, []) if text == "ROOT" else start(text, here))
        assert links._inside(root, str(alias)) == "."
        assert links._inside(root, str(alias) + "/sub") == "./sub"


@pytest.mark.parametrize("path", ["missing/../sub/f.txt", "real.yaml/../sub/f.txt"])
def test_a_dotdot_after_a_missing_name_or_a_file_is_no_path_in_both_trees(tmp_path, path):
    """#1084: as in a checkout (ENOENT / ENOTDIR), ``..`` goes up only from a directory."""
    root = _repo(tmp_path, {})
    for tree in _trees(root):
        with pytest.raises(SourceError, match="no such path"):
            tree.resolve(path)
        assert not tree.is_file(path)


def test_the_link_walk_lives_apart_so_source_stays_under_the_line_cap():
    """The walk is in ``links``; ``source`` re-exports the errors, and its 800-line cap (scripts/check_growth.py) holds."""
    assert source._walk_links is links._walk_links
    assert source.SourceArgumentError.__module__ == "dokimi.repos.source_errors"
    assert len(Path(source.__file__).read_text(encoding="utf-8").splitlines()) <= 800
