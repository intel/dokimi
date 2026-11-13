# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A path git prints with a byte that is not UTF-8 (#550): Windows Python's ``os.fsdecode`` raises on it, so the
readers of git's ``-z`` listings of tracked paths decode with ``process_io.path_text``, which puts U+FFFD there; the
listings of the disk (untracked, ignored) use ``disk_path_text``, which keeps an encoded lone surrogate as the file
system names it; the index readers key by ``exact_path_text`` and skip a name Windows cannot decode. The tests decode
as Windows does (UTF-8, ``surrogatepass``)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi import process_io, utils
from dokimi.external import catalog_git

BAD = b"l\xffint/SKILL.md"
REPLACED = "l�int/SKILL.md"


def _git(root: Path, *args: str | bytes, stdin: bytes | None = None) -> bytes:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env,
                          input=stdin).stdout


def _index_entry(root: Path, mode: str, name: bytes, content: bytes = b"odd\n") -> None:
    """An index entry for ``name`` with no file on disk, fed through stdin: a raw byte cannot be a Windows argument."""
    blob = _git(root, "hash-object", "-w", "--stdin", stdin=content).strip()
    _git(root, "update-index", "--add", "-z", "--index-info", stdin=f"{mode} {blob.decode()}\t".encode() + name + b"\0")


@pytest.fixture
def repo(tmp_path) -> Path:
    """``skill/SKILL.md`` committed, and ``l\\xffint/SKILL.md`` in the base commit and the index with no file on disk."""
    root = tmp_path / "repo"
    (root / "skill").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "skill" / "SKILL.md").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _index_entry(root, "100644", BAD)
    _git(root, "commit", "-q", "-m", "base")
    (root / "skill" / "SKILL.md").write_text("two\n", encoding="utf-8")
    _git(root, "add", "skill/SKILL.md")  # not -a: that would stage the deletion of the odd name
    _git(root, "commit", "-q", "-m", "edit")
    return root.resolve()


@pytest.fixture
def windows_decoding(monkeypatch):
    """``os.fsdecode`` as Windows Python does it, on a platform that reports itself as Windows."""
    monkeypatch.setattr(os, "fsdecode", lambda raw: os.fspath(raw) if isinstance(raw, str) else raw.decode("utf-8", "surrogatepass"))
    monkeypatch.setattr(process_io, "_POSIX", False)


def test_the_fixture_decodes_as_windows_does(windows_decoding):
    with pytest.raises(UnicodeDecodeError):
        os.fsdecode(BAD)


def test_git_tree_holds_a_path_that_is_not_utf8(repo, windows_decoding):
    tree = utils.git_tree(repo / "skill")
    assert tree is not None
    assert REPLACED not in tree.tracked and "skill/SKILL.md" in tree.tracked  # the replaced name is no file's
    assert "skill/SKILL.md" in tree.indexed


def test_tree_digest_with_git_reads_an_index_with_a_path_that_is_not_utf8(repo, windows_decoding):
    digest = utils.tree_digest(repo / "skill", git=True)
    assert len(digest) == 64 and digest == utils.tree_digest(repo / "skill", git=True)


@pytest.fixture
def colliding_repo(tmp_path) -> Path:
    """``l�int/SKILL.md`` a real file (100644, unexecutable) and ``l\\xffint/SKILL.md`` in the index only (100755):
    both decode to the same Windows name."""
    root = tmp_path / "colliding"
    (root / REPLACED.split("/")[0]).mkdir(parents=True)
    _git(root, "init", "-q")
    (root / REPLACED).write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _index_entry(root, "100755", BAD)
    _git(root, "commit", "-q", "-m", "base")
    return root.resolve()


def test_a_name_that_collides_after_replacement_does_not_set_the_mode_of_a_real_file(colliding_repo, windows_decoding):
    assert utils._tracked_modes(colliding_repo, ".") == {REPLACED: False}


def test_the_digest_ignores_the_mode_of_a_name_that_collides_after_replacement(colliding_repo, windows_decoding):
    plain = utils.tree_digest(colliding_repo, git=True, tracked={REPLACED: False})
    assert utils.tree_digest(colliding_repo, git=True) == plain


def test_a_name_that_collides_after_replacement_gives_the_real_file_its_own_index_blob(colliding_repo, windows_decoding):
    """The missing file's status entry is not read as the real file's: the real file, unchanged, keeps its own blob, never
    the other entry's."""
    blobs, held = utils._index_blobs(os.fspath(colliding_repo))
    assert blobs == {REPLACED: _git(colliding_repo, "hash-object", REPLACED).decode().strip()}
    assert REPLACED.split("/")[0] in held


def test_a_changed_file_with_an_encoded_surrogate_name_takes_no_index_blob(tmp_path, windows_decoding):
    """Status and the index must name the file alike, or its stale index blob stands for the changed file (here missing
    from disk, which status reports as `` D``)."""
    root = tmp_path / "edited"
    root.mkdir()
    _git(root, "init", "-q")
    _index_entry(root, "100644", b"skill/\xed\xa0\x80.sh")
    assert utils._index_blobs(os.fspath(root))[0] == {}


def test_the_status_and_flag_readers_name_a_file_with_an_encoded_surrogate_as_the_index_does(windows_decoding):
    assert process_io.worktree_changed(b" M skill/\xed\xa0\x80.sh\0") == {"skill/\ud800.sh"}
    assert process_io.flagged(b"S skill/\xed\xa0\x80.sh\0h skill/b\0H skill/c\0") == {"skill/\ud800.sh", "skill/b"}


@pytest.mark.skipif(os.name != "posix" or sys.platform == "darwin", reason="a file name that is not UTF-8")
def test_git_tree_holds_an_ignored_path_that_is_not_utf8(repo, windows_decoding):
    (repo / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    with open(os.fsencode(repo) + b"/skill/a\xff.tmp", "wb") as handle:
        handle.write(b"x")
    tree = utils.git_tree(repo / "skill")
    assert tree is not None and "skill/a�.tmp" in tree.ignored


SURROGATE_FILE = b"/skill/l\xed\xa0\x80int.tmp"


@pytest.fixture
def surrogate_disk_repo(repo) -> Path:
    """An untracked, ignored file named with an encoded lone surrogate, which Windows names ``l\\ud800int.tmp``."""
    (repo / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    with open(os.fsencode(repo) + SURROGATE_FILE, "wb") as handle:
        handle.write(b"x")
    return repo


@pytest.mark.skipif(os.name != "posix" or sys.platform == "darwin", reason="a file name that is not UTF-8")
def test_untracked_listings_name_a_lone_surrogate_file_as_the_disk_does(surrogate_disk_repo, windows_decoding):
    name = "skill/l\ud800int.tmp"
    assert name in catalog_git._others(surrogate_disk_repo)
    assert name in catalog_git.ignored_paths(surrogate_disk_repo)


@pytest.mark.skipif(os.name != "posix" or sys.platform == "darwin", reason="a file name that is not UTF-8")
def test_git_tree_ignored_directory_with_a_lone_surrogate_name_keeps_its_name(repo, windows_decoding):
    (repo / ".gitignore").write_text("*cache/\n", encoding="utf-8")
    os.mkdir(os.fsencode(repo) + b"/skill/\xed\xa0\x80cache")
    with open(os.fsencode(repo) + b"/skill/\xed\xa0\x80cache/f", "wb") as handle:
        handle.write(b"x")
    assert utils._ignored_paths(str(repo)) == {"skill/\ud800cache"}


def test_a_listed_name_windows_cannot_decode_is_still_replaced(windows_decoding):
    assert process_io.disk_path_text(b"skill/\xed\xa0\x80.sh") == "skill/\ud800.sh"
    assert process_io.disk_path_text(BAD) == REPLACED


def test_the_index_readers_skip_a_name_that_is_not_utf8(windows_decoding):
    assert process_io.worktree_changed(b" M " + BAD + b"\0") == set()
    assert process_io.flagged(b"S " + BAD + b"\0H x\0") == set()


def test_tracked_modes_keeps_a_path_that_is_not_utf8(repo, windows_decoding):
    assert utils._tracked_modes(repo, ".") == {"skill/SKILL.md": False}


def test_catalog_paths_report_a_path_that_is_not_utf8(repo, windows_decoding):
    listed = catalog_git._paths(catalog_git._git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD~1"))
    assert listed == [REPLACED, "skill/SKILL.md"]


def test_changed_from_a_base_with_a_path_that_is_not_utf8_checks_instead_of_failing(repo, windows_decoding):
    _source, paths, _untracked, owners, reason = catalog_git.source_and_changes(repo, "HEAD~1", repo / "out")
    assert paths is None and owners == {} and reason is not None and "every skill" in reason


TASK = "evaluation/harbor/tasks/l"


def _owners_repo(root: Path, odd: bytes) -> Path:
    """Task ``l<odd>int`` (skill alpha) is in the base and the index with no file; the real task ``l�int`` (beta) is
    committed."""
    (root / (TASK + "�int")).mkdir(parents=True)
    _git(root, "init", "-q")
    (root / (TASK + "�int") / "task.toml").write_text('[metadata]\nskill = "beta"\n', encoding="utf-8")
    _git(root, "add", "-A")
    _index_entry(root, "100644", TASK.encode() + odd + b"int/task.toml", b'[metadata]\nskill = "alpha"\n')
    _git(root, "commit", "-q", "-m", "base")
    return root.resolve()


@pytest.fixture
def owners_repo(tmp_path) -> Path:
    """The deleted task is ``l\\xffint``: it and ``l�int`` decode to one Windows name."""
    return _owners_repo(tmp_path / "owners", b"\xff")


@pytest.fixture
def surrogate_owners_repo(tmp_path) -> Path:
    """The deleted task is ``l\\xed\\xa0\\x80int``, an encoded lone surrogate: Windows names it ``l\\ud800int``, yet the
    lossy decoding of its path is ``l�int``, the real task's."""
    return _owners_repo(tmp_path / "surrogate-owners", b"\xed\xa0\x80")


def test_a_changed_task_whose_name_collides_after_replacement_selects_every_skill(owners_repo, windows_decoding):
    """The deleted ``l\\xffint`` task would be read as ``l�int`` and owned by beta, leaving alpha unselected."""
    _source, paths, _untracked, owners, reason = catalog_git.source_and_changes(owners_repo, "HEAD", owners_repo / "out")
    assert paths is None and owners == {} and reason is not None and "every skill" in reason


def test_a_changed_task_named_with_an_encoded_surrogate_selects_every_skill(surrogate_owners_repo, windows_decoding):
    """Its path decodes exactly but lists as ``l�int``'s: beta's owner would stand for alpha's deleted task."""
    root = surrogate_owners_repo
    _source, paths, _untracked, owners, reason = catalog_git.source_and_changes(root, "HEAD", root / "out")
    assert paths is None and owners == {} and reason is not None and "every skill" in reason


@pytest.mark.skipif(os.name != "posix", reason="POSIX keeps the byte; Windows replaces it")
def test_a_posix_changed_task_with_a_byte_that_is_not_utf8_keeps_its_own_owner(owners_repo):
    _source, paths, _untracked, owners, reason = catalog_git.source_and_changes(owners_repo, "HEAD", owners_repo / "out")
    bad = os.fsdecode(TASK.encode() + b"\xffint")
    assert reason is None and paths == [bad + "/task.toml"] and owners == {bad: "alpha"}


@pytest.fixture
def nested_repo(tmp_path) -> Path:
    """The outer index holds ``l\\xffint/SKILL.md`` only; the real directory ``l�int`` is a repository that ignores
    its untracked ``SKILL.md``."""
    root = tmp_path / "nested"
    inner = root / REPLACED.split("/")[0]
    inner.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(inner, "init", "-q")
    (inner / ".gitignore").write_text("SKILL.md\n", encoding="utf-8")
    (inner / "SKILL.md").write_text("ignored\n", encoding="utf-8")
    _index_entry(root, "100644", BAD)
    return root.resolve()


def test_an_index_name_that_collides_after_replacement_does_not_unignore_a_nested_file(nested_repo, windows_decoding):
    tree = utils.git_tree(nested_repo)
    assert tree is not None and REPLACED not in tree.tracked
    assert tree.skipped(nested_repo / REPLACED) is True


def test_exact_path_text_decodes_an_encoded_surrogate_as_windows_does(windows_decoding):
    assert process_io.exact_path_text(b"skill/\xed\xa0\x80.sh") == "skill/\ud800.sh"
    assert process_io.exact_path_text(BAD) is None


def test_tracked_modes_keeps_the_mode_of_a_name_with_an_encoded_surrogate(tmp_path, windows_decoding):
    root = tmp_path / "surrogate"
    root.mkdir()
    _git(root, "init", "-q")
    _index_entry(root, "100755", b"skill/\xed\xa0\x80.sh")
    assert utils._tracked_modes(root, ".") == {"skill/\ud800.sh": True}


@pytest.mark.skipif(os.name != "posix", reason="POSIX keeps the byte; Windows replaces it")
def test_a_posix_decoding_still_names_the_file_by_its_own_bytes(repo):
    listed = catalog_git._paths(catalog_git._git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD~1"))
    assert listed == [os.fsdecode(BAD), "skill/SKILL.md"]


GUIDED_BAD = b"l\xffint/AGENTS.md"
GUIDED_REAL = "l�int/AGENTS.md"


@pytest.fixture
def guided_repo(tmp_path) -> Path:
    """``l�int/AGENTS.md`` a link to ``README.md`` and ``l\\xffint/AGENTS.md`` a plain file: both decode to one Windows
    name, and only the real one is a link."""
    root = tmp_path / "guided"
    (root / GUIDED_REAL.split("/")[0]).mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / GUIDED_REAL).symlink_to("../README.md")
    _git(root, "add", "-A")
    _index_entry(root, "100644", GUIDED_BAD, b"odd\n")
    _git(root, "commit", "-q", "-m", "base")
    return root.resolve()


def _guidance(root: Path):
    from types import SimpleNamespace

    from dokimi.repos import workspace

    manifest = SimpleNamespace(run_paths=lambda: (".eval", ".eval/x"), guidance_globs=("**/AGENTS.md",))
    return workspace._guidance_at(root, manifest, _git(root, "rev-parse", "HEAD").decode().strip())  # type: ignore[arg-type]


def test_guidance_snapshot_keeps_the_real_file_when_a_name_collides_after_replacement(guided_repo, windows_decoding):
    """The real file keeps its own mode and blob: the other name's plain file would hide its link."""
    assert _guidance(guided_repo) == ([GUIDED_REAL], {GUIDED_REAL: "README.md"}, {GUIDED_REAL: ["README.md"]})


def test_guidance_link_to_a_name_windows_cannot_decode_is_refused(tmp_path, windows_decoding):
    from dokimi.repos.errors import PrepError

    root = tmp_path / "linked"
    (root / "l�int").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / GUIDED_REAL).write_text("real\n", encoding="utf-8")
    _git(root, "add", "-A")
    _index_entry(root, "120000", b"AGENTS.md", GUIDED_BAD)
    _git(root, "commit", "-q", "-m", "base")
    with pytest.raises(PrepError, match="not UTF-8"):
        _guidance(root.resolve())


@pytest.mark.skipif(os.name != "posix", reason="POSIX keeps the byte; Windows replaces it")
def test_guidance_snapshot_on_posix_lists_both_names(guided_repo):
    found, links, _ = _guidance(guided_repo)
    assert found == sorted([GUIDED_REAL, os.fsdecode(GUIDED_BAD)]) and list(links) == [GUIDED_REAL]
