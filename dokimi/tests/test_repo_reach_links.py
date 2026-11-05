# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Overlay links in the L1 reach probe: which names a harness is read as, and what that pins (no LLM)."""

from __future__ import annotations

from dokimi.artifacts import validate_artifact
from dokimi.repos import load_manifest
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
