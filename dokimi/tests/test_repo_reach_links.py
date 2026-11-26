# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Overlay links in the L1 reach probe: which names a harness is read as, and what that pins (no LLM)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.repos import SourceError, WorkTree, load_manifest
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.reach import run_reach
from test_repo_reach import _commit_links, _profile, _repo, _write


def test_reach_payload_leaves_a_native_alias_of_a_foreign_overlay_unpinned(tmp_path, monkeypatch):
    """run_reach gives overlay_report the profile's native names: CLAUDE.md -> AGENTS.codex.md is read as the
    native file, so a load of it is not a load of the foreign overlay."""
    monkeypatch.setenv("FAKE_MODE", "ok")
    root = _repo(tmp_path)
    _write(root, "AGENTS.codex.md", "# codex overlay\n")
    (root / "CLAUDE.md").unlink()
    (root / "sub/CLAUDE.md").unlink()
    tree = _commit_links(root, {"CLAUDE.md": "AGENTS.codex.md"})
    payload = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                        profile=load_profile(_profile(tmp_path, overlay="claude")), model="m",
                        out_dir=tmp_path / "out", work_dir=tmp_path, salt="s", kinds=("root",))
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    assert payload["overlays"]["cross_loaded"] == {}
    assert payload["overlays"]["ambiguous"] == ["AGENTS.codex.md", "CLAUDE.md"]


needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
ROOT = Path(__file__).resolve().parents[1]


def _dry_run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), "repo", "reach", "--dry-run",
                           "--model", "claude-opus-4-5", str(repo)],
                          capture_output=True, text=True, cwd=ROOT, timeout=30)


@needs_fifo
def test_reach_dry_run_of_a_fifo_only_guidance_does_not_call_it_a_link(tmp_path):
    """#1273: an AGENTS.md that is a FIFO is no link, so the refusal says it is not a regular file."""
    repo = tmp_path / "fifo-only"
    repo.mkdir()
    os.mkfifo(repo / "AGENTS.md")
    run = _dry_run(repo)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "links to nothing" not in run.stdout + run.stderr
    assert "each is not a regular file: AGENTS.md" in run.stdout + run.stderr


@needs_fifo
def test_reach_dry_run_skips_a_nested_fifo_guidance_file(tmp_path):
    """#1273: sub/AGENTS.md as a FIFO is left out of the plan, as an unfollowable guidance file is."""
    repo = tmp_path / "fifo-nested"
    (repo / "sub").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("hi\n", encoding="utf-8")
    os.mkfifo(repo / "sub" / "AGENTS.md")
    run = _dry_run(repo)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '"AGENTS.md"' in run.stdout
    assert "sub/AGENTS.md" not in run.stdout


@needs_fifo
def test_a_worktree_lists_regular_files_and_links_but_no_fifo(tmp_path):
    """#1273: files() of a plain directory keeps a regular file and a link, and names a FIFO in not_regular."""
    (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "ln.md").symlink_to("a.md")
    os.mkfifo(tmp_path / "pipe.md")
    tree = WorkTree(tmp_path)
    assert tree.files() == ["a.md", "ln.md"]
    assert tree.not_regular == ["pipe.md"]


@needs_fifo
@pytest.mark.parametrize("name", ["pipe.md", "ln.md"])
def test_resolve_names_a_fifo_as_not_a_regular_file(tmp_path, name):
    """#1273: a FIFO, or a link to one, is refused as `is not a regular file`; a missing target stays `links to nothing`."""
    os.mkfifo(tmp_path / "pipe.md")
    (tmp_path / "ln.md").symlink_to("pipe.md")
    (tmp_path / "gone.md").symlink_to("missing.md")
    tree = WorkTree(tmp_path)
    with pytest.raises(SourceError, match="is not a regular file"):
        tree.resolve(name)
    with pytest.raises(SourceError, match="links to nothing"):
        tree.resolve("gone.md")
