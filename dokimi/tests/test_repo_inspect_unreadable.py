# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""#1271: ``repo inspect`` reports inputs it cannot read instead of crashing, listing them silently or calling the tree empty."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.repos.environment import inspect_repo
from dokimi.repos.source import SourceArgumentError, WorkTree

PROJECT = Path(__file__).resolve().parents[1]
NOT_ROOT = pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                              reason="needs a directory mode the user cannot bypass")
FIFO = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
DEV_ZERO = pytest.mark.skipif(not Path("/dev/zero").exists(), reason="needs /dev/zero")


def _cli(root: Path, *extra: str):
    return subprocess.run([sys.executable, "cli.py", "repo", "inspect", str(root), *extra], cwd=PROJECT,
                          capture_output=True, text=True, timeout=60)


def _data(root: Path) -> dict:
    done = _cli(root, "--json")
    assert "Traceback" not in done.stderr, done.stderr[-600:]
    return json.loads(done.stdout)["data"]


def _said(data: dict, name: str) -> bool:
    return name in json.dumps(data["findings"] + data["not_observed"]) or any(
        c["path"] == name for c in data.get("capabilities", []))


def _listed_silently(data: dict, name: str) -> bool:
    return any(i["path"] == name for i in data["instructions"]) and not _said(data, name)


@NOT_ROOT
def test_1_link_into_unreadable_directory(tmp_path):
    """#1271: a link into a directory that cannot be searched is listed and named, not a PermissionError."""
    root = tmp_path / "repo"
    (root / "locked").mkdir(parents=True)
    (root / "locked" / "agents.md").write_text("# Guide\n", encoding="utf-8")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "AGENTS.md").symlink_to("locked/agents.md")
    (root / "locked").chmod(0o000)
    try:
        done = _cli(root)
    finally:
        (root / "locked").chmod(0o755)
    assert "Traceback" not in done.stderr, done.stderr[-600:]
    assert done.returncode == 0, done.stderr[-600:]
    assert "1 instruction files (1 root)" in done.stdout


@NOT_ROOT
def test_2_unreadable_root_is_refused(tmp_path):
    """#1271: a source directory that cannot be listed is refused, not reported as an empty tree."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    root.chmod(0o000)
    try:
        done = _cli(root)
    finally:
        root.chmod(0o755)
    assert "Traceback" not in done.stderr, done.stderr[-600:]
    assert "0 instruction files" not in done.stdout
    assert done.returncode in (1, 2), (done.returncode, done.stdout[-300:])


@NOT_ROOT
def test_3_mode_444_directory(tmp_path):
    """#1271: a directory without the search bit is refused with exit 2 and its name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("hi\n", encoding="utf-8")
    os.chmod(repo, 0o444)
    try:
        run = _cli(repo)
    finally:
        repo.chmod(0o755)
    assert "Traceback" not in run.stderr, run.stderr[-600:]
    assert run.returncode == 2, run.stdout + run.stderr
    assert "repo" in run.stdout + run.stderr


def test_4_dangling_agents_link(tmp_path):
    """#1271: a dangling instruction link is named in not_observed."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to("nowhere.md")
    rep = inspect_repo(WorkTree(tmp_path))
    assert (any(f["rule"] == "instructions.missing" or f["path"] == "AGENTS.md" for f in rep["findings"])
            or any("AGENTS.md" in n for n in rep["not_observed"]))


def test_5_empty_agents_md(tmp_path):
    """#1271: an empty instruction file is named in not_observed."""
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")
    data = _data(tmp_path)
    assert not _listed_silently(data, "AGENTS.md"), data["findings"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n", *args], cwd=repo, check=True, capture_output=True)


def _git_repo(tmp_path: Path, link: str, target: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "f.txt").write_text("x\n", encoding="utf-8")
    (repo / link).symlink_to(target)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    return repo


@pytest.mark.parametrize("target", ["AGENTS.md", "sub"])
def test_6_self_or_directory_link(tmp_path, target):
    """#1271: a link to itself or to a directory is named, as ``repo scan`` does."""
    repo = _git_repo(tmp_path, "AGENTS.md", target)
    data = _data(repo)
    assert not _listed_silently(data, "AGENTS.md"), data["instructions"]


def test_6_link_loop_at_head(tmp_path):
    """#1271: a link loop at a revision is named."""
    repo = _git_repo(tmp_path, "AGENTS.md", "B")
    (repo / "B").symlink_to("AGENTS.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "y")
    done = _cli(Path(f"{repo}@HEAD"), "--json")
    assert "Traceback" not in done.stderr, done.stderr[-600:]
    data = json.loads(done.stdout)["data"]
    assert not _listed_silently(data, "AGENTS.md"), data["instructions"]


def test_a_looping_plugin_directory_link_does_not_crash_inspect(tmp_path, monkeypatch):
    """#1271: a ``.claude-plugin`` link to itself is no manifest directory; Python before 3.13 raises
    ``RuntimeError`` from ``Path.resolve`` on a loop, which is simulated so every Python is covered."""
    (tmp_path / "AGENTS.md").write_text("x")
    (tmp_path / ".claude-plugin").symlink_to(".claude-plugin")
    real = Path.resolve

    def resolve(self, strict=False):
        try:
            return real(self, strict=strict)
        finally:
            if ".claude-plugin" in self.parts:
                raise RuntimeError(f"Symlink loop from {self!r}")

    monkeypatch.setattr(Path, "resolve", resolve)
    tree = WorkTree(tmp_path)
    rep = inspect_repo(tree)
    assert not any(c["path"].startswith(".claude-plugin") for c in rep["capabilities"])  # a loop is no manifest directory


@DEV_ZERO
def test_7_device_instruction(tmp_path):
    """#1271: a link to a device is named."""
    (tmp_path / "CLAUDE.md").symlink_to("/dev/zero")
    data = _data(tmp_path)
    listed = [i for i in data["instructions"] if i["path"] == "CLAUDE.md"]
    assert not listed or listed[0]["notes"] or _said(data, "CLAUDE.md"), data["instructions"]


@pytest.mark.parametrize("manifest", [".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"])
def test_8_directory_manifest(tmp_path, manifest):
    """#1271: a manifest path that is a directory is reported like a dangling link."""
    (tmp_path / "AGENTS.md").write_text("x")
    (tmp_path / manifest).mkdir(parents=True)
    rep = inspect_repo(WorkTree(tmp_path))
    assert any(c["path"] == manifest for c in rep["capabilities"]) or any(manifest in n for n in rep["not_observed"])


@FIFO
def test_9_fifo_agents_md(tmp_path):
    """#1271: a pipe named AGENTS.md is named in not_observed."""
    os.mkfifo(tmp_path / "AGENTS.md")
    data = _data(tmp_path)
    assert not _listed_silently(data, "AGENTS.md"), data["instructions"]


def test_a_readable_linked_instruction_file_is_not_flagged(tmp_path):
    """#1271: a link to a file with content, and a plain one, raise no note and no not_observed line."""
    (tmp_path / "real.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to("real.md")
    rep = inspect_repo(WorkTree(tmp_path))
    assert not any("AGENTS.md" in n for n in rep["not_observed"])
    assert all(not i["notes"] for i in rep["instructions"] if i["path"] == "AGENTS.md")


def test_an_unusable_instruction_file_carries_a_note(tmp_path):
    """#1271: the instruction record says why the file is not loaded, as well as not_observed."""
    (tmp_path / "AGENTS.md").write_text("", encoding="utf-8")
    rep = inspect_repo(WorkTree(tmp_path))
    assert any("not loaded: is empty" in n for i in rep["instructions"] for n in i["notes"])
    assert any("AGENTS.md instructions (not loaded: is empty)" == n for n in rep["not_observed"])


def test_a_link_to_an_empty_file_is_empty(tmp_path):
    """#1271: the size is the target's, as for a linked file with content."""
    (tmp_path / "real.md").write_text("", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to("real.md")
    rep = inspect_repo(WorkTree(tmp_path))
    assert any("AGENTS.md" in n for n in rep["not_observed"])


@FIFO
def test_resolve_names_a_pipe_not_a_link(tmp_path):
    """#1271: a non-link that is no regular file is not said to link to nothing."""
    os.mkfifo(tmp_path / "p")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(SourceArgumentError, match="p is not a regular file"):
        WorkTree(tmp_path).resolve("p")
    with pytest.raises(SourceArgumentError, match="links to nothing"):
        (tmp_path / "d").symlink_to("nowhere")
        WorkTree(tmp_path).resolve("d")


@NOT_ROOT
def test_resolve_through_an_unsearchable_directory_is_a_source_error(tmp_path):
    """#1271: the permission error becomes SourceArgumentError, which callers already catch."""
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked" / "f").write_text("x", encoding="utf-8")
    (tmp_path / "l").symlink_to("locked/f")
    (tmp_path / "locked").chmod(0o000)
    try:
        with pytest.raises(SourceArgumentError, match="Permission denied"):
            WorkTree(tmp_path).resolve("l")
    finally:
        (tmp_path / "locked").chmod(0o755)


def test_a_plugin_directory_linked_outside_the_tree_does_not_refuse_the_repository(tmp_path):
    """#1271: the manifest probe meets a link out of the tree; the repository is still inspected."""
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    (root / ".claude-plugin").symlink_to(outside)
    done = _cli(root, "--json")
    assert done.returncode == 0, done.stderr[-600:]


def test_text_output_names_an_instruction_file_that_is_not_loaded(tmp_path):
    """#1271: without --json the report says which instruction file was not loaded, and why."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to("missing.md")
    out = _cli(tmp_path).stdout
    assert "NOT LOADED AGENTS.md -- AGENTS.md links to nothing: missing.md" in out, out
    assert "not loaded" not in out, out


def test_a_directory_named_as_a_file_is_not_said_to_be_a_link(tmp_path):
    """#1271: a real directory is "a directory"; only a link is said to link to one."""
    (tmp_path / "AGENTS.md").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "CLAUDE.md").symlink_to("docs")
    tree = WorkTree(tmp_path)
    with pytest.raises(SourceArgumentError, match="^AGENTS.md is a directory$"):
        tree.resolve("AGENTS.md")
    with pytest.raises(SourceArgumentError, match="CLAUDE.md links to a directory: docs"):
        tree.resolve("CLAUDE.md")


def test_reading_a_directory_is_a_source_error_not_an_oserror(tmp_path):
    """#1271: read_text and read_bytes refuse a directory with the same error as a pipe."""
    (tmp_path / "AGENTS.md").mkdir()
    for read in (WorkTree(tmp_path).read_text, WorkTree(tmp_path).read_bytes):
        with pytest.raises(SourceArgumentError, match="AGENTS.md is a directory"):
            read("AGENTS.md")


def test_an_instruction_blob_a_partial_clone_cannot_fetch_is_named(tmp_path):
    """#1271: a read that fails is a reason for the note, not a silent "nothing wrong"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "AGENTS.md").write_text("guide\n", encoding="utf-8")
    _git(repo, "add", "AGENTS.md")
    _git(repo, "commit", "-qm", "guide")
    blob = subprocess.run(["git", "rev-parse", "HEAD:AGENTS.md"], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()
    _git(repo, "config", "remote.origin.promisor", "true")
    _git(repo, "config", "remote.origin.url", str(tmp_path / "nowhere"))
    obj = repo / ".git" / "objects" / blob[:2] / blob[2:]
    obj.chmod(0o644)
    obj.unlink()
    data = _data(Path(f"{repo}@HEAD"))
    assert _said(data, "AGENTS.md"), data["instructions"]


@FIFO
def test_a_tracked_instruction_file_replaced_by_a_pipe_is_still_listed(tmp_path):
    """#1271: the git listing keeps a pipe in place of a tracked file, as the plain listing does."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "AGENTS.md").write_text("# Guide\n", encoding="utf-8")
    _git(repo, "add", "-A")
    (repo / "AGENTS.md").unlink()
    os.mkfifo(repo / "AGENTS.md")
    assert "AGENTS.md" in WorkTree(repo).files()
    data = _data(repo)
    assert _said(data, "AGENTS.md"), data["instructions"]


def test_an_empty_ignored_link_target_is_empty(tmp_path):
    """#1271: the target is sized even when git leaves it out of the listing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("private.md\n", encoding="utf-8")
    (repo / "private.md").write_text("", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to("private.md")
    data = _data(repo)
    assert _said(data, "AGENTS.md"), data["instructions"]


@FIFO
def test_a_link_to_a_pipe_is_not_a_link_to_nothing(tmp_path):
    """#1271: a link to something that exists but is no regular file says so."""
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "AGENTS.md").symlink_to("pipe")
    with pytest.raises(SourceArgumentError, match="not a regular file"):
        WorkTree(tmp_path).resolve("AGENTS.md")


def _fifo_in_git(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "AGENTS.md").write_text("guide\n", encoding="utf-8")
    _git(repo, "add", "AGENTS.md")
    (repo / "AGENTS.md").unlink()
    os.mkfifo(repo / "AGENTS.md")
    return repo


def _plain_fifo(tmp_path: Path) -> Path:
    repo = tmp_path / "plain"
    repo.mkdir()
    os.mkfifo(repo / "AGENTS.md")
    return repo


@FIFO
@pytest.mark.parametrize("command", ["budget", "scan"])
@pytest.mark.parametrize("kind", ["plain", "git"])
def test_a_pipe_in_the_listing_does_not_hang_budget_or_scan(tmp_path, command, kind):
    """#1271: a pipe in the listing, plain or tracked, is never opened for reading."""
    repo = _plain_fifo(tmp_path) if kind == "plain" else _fifo_in_git(tmp_path)
    try:
        done = subprocess.run([sys.executable, "cli.py", "repo", command, str(repo)], cwd=PROJECT,
                              capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(f"repo {command} blocked on the pipe")
    assert "Traceback" not in done.stderr, done.stderr[-600:]


@NOT_ROOT
def test_budget_refuses_an_unreadable_root(tmp_path):
    """#1271: every `repo` command refuses a source directory it cannot list, with exit 2."""
    root = tmp_path / "repo"
    root.mkdir()
    root.chmod(0o000)
    try:
        done = subprocess.run([sys.executable, "cli.py", "repo", "budget", str(root)], cwd=PROJECT,
                              capture_output=True, text=True, timeout=60)
    finally:
        root.chmod(0o755)
    assert "Traceback" not in done.stderr, done.stderr[-600:]
    assert done.returncode == 2, done.stderr[-600:]


def test_an_empty_instruction_file_is_named_in_a_partial_clone(tmp_path):
    """#1271: the empty check does not depend on the sizes a partial clone cannot list."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "AGENTS.md").write_text("", encoding="utf-8")
    _git(repo, "add", "AGENTS.md")
    _git(repo, "commit", "-qm", "empty")
    _git(repo, "config", "remote.origin.promisor", "true")
    data = _data(Path(f"{repo}@HEAD"))
    assert _said(data, "AGENTS.md"), data["instructions"]


@NOT_ROOT
@pytest.mark.parametrize("linked", [False, True])
def test_an_instruction_file_of_mode_000_is_named(tmp_path, linked):
    """#1271: a file the harness cannot open is not counted as loadable guidance."""
    real = tmp_path / ("real.md" if linked else "AGENTS.md")
    real.write_text("guide\n", encoding="utf-8")
    if linked:
        (tmp_path / "AGENTS.md").symlink_to("real.md")
    real.chmod(0o000)
    try:
        data = _data(tmp_path)
    finally:
        real.chmod(0o644)
    assert _said(data, "AGENTS.md"), data["instructions"]
