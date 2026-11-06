# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""skill_sha and suite_hash hash what git stores: blob ids, no ignored paths (a nested repository's too), no nested
``.git``."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from dokimi import utils
from dokimi.repo_config import ConfigError, load_repo_config
from dokimi.utils import git_tree, tree_digest

HEADER = "schema_version: dokimi_config.v1\n"
CONFIG = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: skills/a/evals\n"
FILES = {"skills/a/SKILL.md": "a\nb\n", "skills/a/evals/t/test.bat": "echo a\necho b\n", "skills/a/evals/q.json": "[]\n"}


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _repo(root: Path, files: dict[str, str]) -> Path:
    """A committed git repository at ``root`` holding ``files`` (LF) and the config; returns its resolved path."""
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    files = {".github/dokimi/config.yaml": CONFIG, ".gitignore": "__pycache__/\n.DS_Store\n", **files}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root.resolve()


def _hashes(root: Path) -> tuple[str, str]:
    subject = load_repo_config(root).subjects()[0]
    return subject.skill_sha, subject.suite_hash


def test_ignored_files_do_not_move_skill_sha_or_suite_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    for rel in ("skills/a/__pycache__/x.pyc", "skills/a/evals/__pycache__/y.pyc", "skills/a/.DS_Store"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"ignored\n")
    assert _hashes(root) == before
    assert subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True,
                          text=True, check=True).stdout == ""


def test_a_nested_git_directory_does_not_move_skill_sha(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    _git(root / "skills" / "a", "init", "-q")  # a nested repository's own .git holds no content of the skill
    assert _hashes(root)[0] == before[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_file_a_nested_repository_ignores_does_not_move_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    inner = root / "skills/a/vendor"
    inner.mkdir()
    _git(inner, "init", "-q")
    (inner / ".gitignore").write_text("*.pyc\n")
    (inner / "lib.py").write_text("x = 1\n")
    _git(inner, "add", "-A")
    _git(inner, "commit", "-q", "-m", "inner")
    before = _hashes(root)
    (inner / "lib.pyc").write_bytes(b"\0compiled\n")  # the inner repository's .gitignore excludes it
    assert subprocess.run(["git", "-C", str(inner), "status", "--porcelain"], capture_output=True,
                          text=True, check=True).stdout == ""
    assert _hashes(root) == before
    (inner / "lib.py").write_text("x = 2\n")  # its tracked files still count
    assert _hashes(root)[0] != before[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_file_the_top_tracks_counts_under_a_nested_ignore_when_git_status_fails(tmp_path):
    root = _repo(tmp_path / "r", {**FILES, "skills/a/vendor/lib.pyc": "one\n"})
    inner = root / "skills/a/vendor"
    _git(inner, "init", "-q")
    (inner / ".gitignore").write_text("*.pyc\n")
    ref = (root / ".git/HEAD").read_text().split()[1]
    (root / ".git" / ref).write_text("1" * 40 + "\n")  # HEAD names a missing commit: status fails, ls-files works
    before = _hashes(root)
    (inner / "lib.pyc").write_text("two\n")
    assert _hashes(root)[0] != before[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("rel", ["lib.pyc", "build/out.txt"])
def test_a_file_the_top_tracks_counts_though_a_nested_repository_ignores_it(tmp_path, rel):
    root = _repo(tmp_path / "r", {**FILES, f"skills/a/vendor/{rel}": "one\n"})
    inner = root / "skills/a/vendor"
    _git(inner, "init", "-q")
    (inner / ".gitignore").write_text("*.pyc\nbuild/\n")
    (inner / "build").mkdir(exist_ok=True)
    before = _hashes(root)
    (inner / "build/new.txt").write_text("new\n")  # the top does not track it: the inner rules leave it out
    assert _hashes(root) == before
    (inner / rel).write_text("two\n")  # the top's index tracks it: git stores the edit
    assert _hashes(root)[0] != before[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_nested_repository_git_cannot_read_fails_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    inner = root / "skills/a/vendor"
    inner.mkdir()
    _git(inner, "init", "-q")
    (inner / ".gitignore").write_text("*.log\n")
    (inner / "x.log").write_text("log\n")
    (inner / ".git/index").write_bytes(b"garbage")  # git sees a repository here, but cannot read it
    with pytest.raises(ConfigError, match="vendor: git cannot read this nested repository"):
        _hashes(root)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_top_repository_git_cannot_list_hashes_the_bytes_on_disk(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    (root / "skills/a/__pycache__").mkdir()
    (root / "skills/a/__pycache__/x.pyc").write_bytes(b"ignored\n")
    (root / ".git/index").write_bytes(b"garbage")  # git still finds the top, but cannot list what it ignores
    assert git_tree(root) is None
    assert tree_digest(root / "skills/a", git=True) == tree_digest(root / "skills/a", git=None)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold a newline, a quote or a CR")
@pytest.mark.parametrize("name", ["we\nird.txt", '"quoted.txt', "cr.txt\r"])
def test_a_name_git_stdin_misreads_digests_the_same_edited_and_committed(tmp_path, name):
    root = _repo(tmp_path / "r", {**FILES, name: "one\n"})  # at the top: git reads a top-relative name
    (root / name).write_text("two\n")
    edited = tree_digest(root, git=True)
    _git(root, "commit", "-qam", "two")
    assert tree_digest(root, git=True) == edited  # the same content: the blob id both ways, not raw bytes once edited


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold a newline")
def test_odd_names_beyond_one_command_line_digest_the_same_edited_and_committed(tmp_path, monkeypatch):
    monkeypatch.setattr("dokimi.utils._ARG_BUDGET", 120)  # a few names per git call: several batches
    names = {f"we\nird{i}.txt": "one\n" for i in range(12)}
    root = _repo(tmp_path / "r", {**FILES, **names})
    for name in names:
        (root / name).write_text("two\n")
    edited = tree_digest(root, git=True)
    _git(root, "commit", "-qam", "two")
    assert tree_digest(root, git=True) == edited


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold a newline")
def test_a_failed_batch_of_odd_names_hashes_their_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr("dokimi.utils._ARG_BUDGET", 30)
    root = _repo(tmp_path / "r", {**FILES, **{f"we\nird{i}.txt": "one\n" for i in range(4)}})
    odd = [p for p in root.iterdir() if "\n" in p.name]
    for path in odd:
        path.write_text("two\n")  # edited: git's index no longer holds the blob id
    tree = git_tree(root)
    real = utils._hash_objects
    calls = []

    def second_fails(top, rels, *, as_args):
        calls.append(as_args)
        if as_args and calls.count(True) == 2:
            return None
        return real(top, rels, as_args=as_args)

    monkeypatch.setattr(utils, "_hash_objects", second_fails)
    got = utils.file_contents(odd, tree)
    assert calls.count(True) == 2  # the batches were tried in turn, and the failure stopped them
    assert got == {p: hashlib.sha256(p.read_bytes()).digest() for p in odd}


def test_a_committed_lf_to_crlf_change_moves_suite_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    bat = root / "skills/a/evals/t/test.bat"
    bat.write_bytes(b"echo a\r\necho b\r\n")
    _git(root, "commit", "-qam", "crlf")
    after = _hashes(root)
    assert after[1] != before[1]  # git sees the change, so the suite hash must


def test_a_crlf_checkout_with_autocrlf_hashes_as_the_lf_clone(tmp_path):
    src = _repo(tmp_path / "src", FILES)
    clone = tmp_path / "clone"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(src), str(clone)], check=True,
                   capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    assert b"\r\n" in (clone / "skills/a/evals/t/test.bat").read_bytes()  # the checkout really is CRLF
    assert _hashes(clone.resolve()) == _hashes(src)


def test_a_file_that_is_not_in_git_is_hashed_as_its_bytes(tmp_path):
    # outside a work tree nothing is ignored and no line end is folded: the fallback is the bytes on disk
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    for root, newline in ((lf, b"\n"), (crlf, b"\r\n")):
        (root / "skills/a/evals").mkdir(parents=True)
        (root / ".github/dokimi").mkdir(parents=True)
        (root / ".github/dokimi/config.yaml").write_text(CONFIG)
        (root / "skills/a/SKILL.md").write_bytes(b"a" + newline)
        (root / "skills/a/evals/q.json").write_bytes(b"[]" + newline)
    assert _hashes(lf) != _hashes(crlf)
    before = _hashes(lf)
    (lf / "skills/a/__pycache__").mkdir()
    (lf / "skills/a/__pycache__/x.pyc").write_bytes(b"x")
    assert _hashes(lf) != before


def test_a_new_untracked_file_moves_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    (root / "skills/a/notes.md").write_text("not ignored\n")
    assert _hashes(root)[0] != before[0]


def test_a_committed_crlf_file_hashes_the_same_in_an_autocrlf_clone(tmp_path):
    src = _repo(tmp_path / "src", {**FILES, "skills/a/evals/t/test.bat": "echo a\r\necho b\r\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    assert _hashes(clone.resolve()) == _hashes(src)  # git keeps the committed CRLF blob in both clones


def test_an_ignored_directory_above_the_root_does_not_hide_its_files(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    (outer / ".gitignore").write_text("build/\n")
    root = outer / "build" / "skills-copy"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_text("a\n")
    _git(outer, "add", "-A")
    _git(outer, "commit", "-q", "-m", "init")
    before = tree_digest(root, git=git_tree(root))
    (root / "a" / "SKILL.md").write_text("b\n")
    assert tree_digest(root, git=git_tree(root)) != before  # build/ is git's, not the walk's: SKILL.md still counts


def test_a_touched_crlf_file_keeps_its_hash_in_an_autocrlf_clone(tmp_path):
    src = _repo(tmp_path / "src", {**FILES, "skills/a/evals/t/test.bat": "echo a\r\necho b\r\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    later = time.time() + 60  # the same bytes, a clearly newer mtime (git would then compare the content)
    os.utime(clone / "skills/a/evals/t/test.bat", (later, later))
    assert _hashes(clone.resolve()) == _hashes(src)


def test_an_edit_under_skip_worktree_moves_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    _git(root, "update-index", "--skip-worktree", "skills/a/SKILL.md")
    (root / "skills/a/SKILL.md").write_text("changed\n")
    assert _hashes(root) != before  # git does not look at a skip-worktree file; the hash must still read the disk


def test_an_edit_under_assume_unchanged_moves_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    _git(root, "update-index", "--assume-unchanged", "skills/a/SKILL.md")
    (root / "skills/a/SKILL.md").write_text("changed\n")
    assert _hashes(root) != before  # git status skips the file; its index blob is stale


REPO_CONFIG = HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: m.yaml\n    suite: evals\n"
REPO_FILES = {".github/dokimi/config.yaml": REPO_CONFIG, "m.yaml": "a: 1\n", "evals/t/test.bat": "echo a\n"}


def test_a_repository_suite_hashes_what_git_stores(tmp_path):
    root = _repo(tmp_path / "r", REPO_FILES)
    (root / "m.yaml").chmod(0o755)
    _git(root, "update-index", "--chmod=+x", "m.yaml")
    _git(root, "commit", "-q", "-m", "exec")
    before = _hashes(root)[1]
    (root / "evals/__pycache__").mkdir()
    (root / "evals/__pycache__/x.pyc").write_bytes(b"ignored\n")
    (root / "m.yaml").chmod(0o644)  # the manifest is digested on its own; its bit is the index's
    assert _hashes(root)[1] == before


def test_a_rename_in_the_work_tree_column_hashes_the_work_tree(tmp_path):
    root = _repo(tmp_path / "r", {**FILES, "README.md": "readme\n"})
    (root / "README.md").rename(root / "README2.md")  # an origin path starting with R must not swallow the next entry
    (root / "skills/a/evals/q.json").rename(root / "skills/a/evals/n.json")
    _git(root, "add", "-N", "README2.md", "skills/a/evals/n.json")  # the index holds n.json as an empty blob
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "-uno"], capture_output=True,
                            check=True).stdout
    assert b" R skills/a/evals/n.json\0skills/a/evals/q.json\0" in status
    dirty = _hashes(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "rename")
    assert dirty == _hashes(root)


def _nested(root: Path, rel: str, files: dict[str, str]) -> None:
    """A repository of its own at ``root/rel``: its own ``.gitignore`` (``*.tmp``, ``build/``) and committed ``files``."""
    repo = root / rel
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    for name, text in {".gitignore": "*.tmp\nbuild/\n", **files}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


@pytest.mark.parametrize("rel", ["skills/a/tools", "skills/a/evals/tools"])
def test_an_ignored_file_inside_a_nested_repository_moves_no_hash(tmp_path, rel):
    # the nested repository's own ignore rules apply inside it: skill_sha (outside evals) and suite_hash (inside) both
    root = _repo(tmp_path / "r", FILES)
    _nested(root, rel, {"run.py": "print(1)\n"})
    before = _hashes(root)
    (root / rel / "scratch.tmp").write_text("x\n")
    (root / rel / "build").mkdir()
    (root / rel / "build" / "out.o").write_text("x\n")
    assert _hashes(root) == before


def test_a_directory_outside_any_work_tree_hashes_its_bytes(tmp_path):
    # no .git and git says it is in no work tree: nothing for git to say, so the bytes on disk are hashed
    root = tmp_path / "plain"
    for rel, text in {".github/dokimi/config.yaml": CONFIG, **FILES}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode("utf-8"))
    assert git_tree(root) is None
    before = _hashes(root)
    (root / "skills/a/SKILL.md").write_bytes(b"a\r\nb\r\n")  # bytes on disk, not a git blob id
    assert _hashes(root) != before


@pytest.mark.parametrize("rel", ["skills/a/tools", "skills/a/evals/tools"])
def test_a_tracked_or_untracked_change_inside_a_nested_repository_moves_a_hash(tmp_path, rel):
    root = _repo(tmp_path / "r", FILES)
    _nested(root, rel, {"run.py": "print(1)\n"})
    before = _hashes(root)
    (root / rel / "run.py").write_text("print(2)\n")  # tracked by the nested repository
    assert _hashes(root) != before
    before = _hashes(root)
    (root / rel / "notes.md").write_text("not ignored by the nested repository\n")  # untracked, kept
    assert _hashes(root) != before


@pytest.mark.parametrize("rel", ["skills/a/tools", "skills/a/evals/tools"])
def test_a_change_inside_an_untracked_repository_under_a_nested_repository_moves_a_hash(tmp_path, rel):
    # the nested repository lists its untracked repository as a directory (vendor/inner/); the walk must reach it
    root = _repo(tmp_path / "r", FILES)
    _nested(root, rel, {"run.py": "print(1)\n"})
    _nested(root, f"{rel}/vendor/inner", {"x.py": "x = 1\n"})
    before = _hashes(root)
    (root / rel / "vendor" / "inner" / "x.py").write_text("x = 2\n")
    assert _hashes(root) != before


@pytest.mark.parametrize("rel", ["skills/a/tools", "skills/a/evals/tools"])
def test_a_nested_repository_inside_a_nested_repository_follows_its_own_ignore_rules(tmp_path, rel):
    # the outer nested repository ignores *.tmp (_nested); the innermost one ignores only *.log, so *.tmp is kept there
    root = _repo(tmp_path / "r", FILES)
    _nested(root, rel, {"run.py": "print(1)\n"})
    inner = root / rel / "vendor" / "inner"
    _nested(root, f"{rel}/vendor/inner", {".gitignore": "*.log\n", "x.py": "x = 1\n"})
    before = _hashes(root)
    (inner / "y.log").write_text("x\n")  # ignored by the innermost repository
    assert _hashes(root) == before
    (inner / "z.tmp").write_text("x\n")  # kept: the outer repository's *.tmp rule does not reach inside
    assert _hashes(root) != before
    (inner / "z.tmp").unlink()
    before = _hashes(root)
    (inner / "x.py").write_text("x = 2\n")  # tracked by the innermost repository
    assert _hashes(root) != before


def test_a_submodule_inside_a_nested_repository_is_hashed_by_its_checked_out_files(tmp_path):
    # pinned: the files checked out in the submodule count, not the gitlink commit the skill's index records for it
    rel = "skills/a/tools"
    lib = tmp_path / "lib"
    lib.mkdir()
    _git(lib, "init", "-q")
    (lib / "lib.py").write_text("x = 1\n")
    _git(lib, "add", "-A")
    _git(lib, "commit", "-q", "-m", "lib")
    root = _repo(tmp_path / "r", FILES)
    _nested(root, rel, {"run.py": "print(1)\n"})
    tools = root / rel
    _git(tools, "-c", "protocol.file.allow=always", "submodule", "add", str(lib), "sub")
    _git(tools, "commit", "-q", "-m", "submodule")
    before = _hashes(root)
    (tools / "sub" / "lib.py").write_text("x = 2\n")  # a file checked out in the submodule
    assert _hashes(root) != before
    (tools / "sub" / "lib.py").write_text("x = 1\n")
    assert _hashes(root) == before
    # a new commit in the submodule moves its HEAD, not the gitlink the skill's index records: the hash must not move
    _git(tools / "sub", "commit", "-q", "--allow-empty", "-m", "same tree")
    assert _hashes(root) == before


def test_a_case_only_rename_in_a_nested_repository_still_moves_skill_sha(tmp_path):
    # PRRT_kwDOVBg7g860MHqZ: under core.ignorecase ls-files spells the tracked name as the index has it, and the file
    # on disk is spelled otherwise; it is not ignored, so an edit to it moves the digest
    root = _repo(tmp_path / "r", FILES)
    rel = "skills/a/tools"
    _nested(root, rel, {"Readme.md": "a\n"})
    _git(root / rel, "config", "core.ignorecase", "true")
    os.rename(root / rel / "Readme.md", root / rel / "README.md")
    before = _hashes(root)
    (root / rel / "README.md").write_text("b\n")
    assert _hashes(root) != before


def test_a_file_the_top_tracks_counts_though_a_nested_glob_ignores_it(tmp_path):
    # PRRT_kwDOVBg7g860MHqk: the top stores skills/a/tools/gen.json, so its rules win over the nested .gitignore
    root = _repo(tmp_path / "r", {**FILES, "skills/a/tools/gen.json": "1\n"})
    _nested(root, "skills/a/tools", {".gitignore": "*.json\n"})
    before = _hashes(root)
    (root / "skills/a/tools/gen.json").write_text("2\n")
    assert _hashes(root) != before
