# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance:overlay arm and the overlay reach check (agentsmd/agents.md#185); offline, no LLM."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.harness_profile import PROFILE_DIR, ProfileError, load_profile, parse_profile
from dokimi.repos.overlays import native_names, overlay_of
from dokimi.repos.reach import format_matrix, overlay_report, probe_files
from dokimi.repos.workspace import (
    ARM_FORMS,
    OVERLAY_ARM,
    SNAPSHOT_AUTHOR,
    PrepError,
    _apply_arm,
    _apply_overlay,
    _git_env,
    export_eval_dir,
    load_tasks,
    parse_arm,
    prepare_workspace,
    source_git_dir,
)
from repo_eval_fixture import git, make_repo, write

OVERLAY_FILES = {
    "AGENTS.md": "# guidance\n",
    "AGENTS.claude.md": "# for claude\n",
    "AGENTS.codex.md": "# for codex\n",
    "sub/AGENTS.md": "# sub guidance\n",
    "sub/AGENTS.copilot.md": "# sub, for copilot\n",
}


def _overlay_base(root: Path, base: str) -> str:
    """A commit on ``base`` that adds the overlay files; the tasks move onto it, and HEAD holds the overlays too."""
    wt = root.parent / "overlay-wt"
    git(root, "worktree", "add", "-q", "-b", "overlays", str(wt), base)
    for rel, text in OVERLAY_FILES.items():
        write(wt, rel, text)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "overlays")
    sha = git(wt, "rev-parse", "HEAD")
    git(root, "worktree", "remove", "--force", str(wt))
    for task in ("fix_add", "review_add"):
        path = root / ".agent-eval" / "tasks" / task / "task.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["base"] = sha
        path.write_text(json.dumps(data), encoding="utf-8")
    for rel, text in OVERLAY_FILES.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "tasks on the overlay base")
    return sha


@pytest.fixture
def repo(tmp_path):
    root, base = make_repo(tmp_path)
    _overlay_base(root, base)
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    content = export_eval_dir(tree, manifest, tmp_path / "content")
    tasks = {t.id: t for t in load_tasks(content, manifest)}
    return {"root": root, "tree": tree, "manifest": manifest, "tasks": tasks,
            "git_dir": source_git_dir(source, tree), "tmp": tmp_path}


def _prep(repo, arm: str, profile, name: str = "rd"):
    meta = prepare_workspace(repo["git_dir"], repo["manifest"], repo["tasks"]["fix_add"], arm,
                             repo["tmp"] / name, profile)
    return meta, repo["tmp"] / name / "workspace"


@pytest.mark.parametrize("path, name", [
    ("AGENTS.claude.md", "claude"),
    ("sub/AGENTS.CODEX.md", "codex"),
    ("docs/AGENTS.copilot.md", "copilot"),
    ("AGENTS.md", None),
    ("AGENTS.override.md", None),  # codex's own file, loaded by its own convention
    ("CLAUDE.md", None),
    ("AGENTS.a.b.md", None),
    ("AGENTS.md.bak", None),
    (".claude/skills/lint/AGENTS.codex.md", None),  # skill content, not guidance
])
def test_overlay_names_are_agent_files_beside_agents_md(path, name):
    assert overlay_of(path) == name


def test_overlay_is_a_guidance_part_and_takes_skills():
    assert "guidance:overlay" in ARM_FORMS
    assert parse_arm("guidance:overlay").base == "guidance:overlay"
    arm = parse_arm("guidance:overlay+skill:skills/lint")
    assert str(arm) == "guidance:overlay+skill:lint" and arm.base == "guidance:overlay"
    with pytest.raises(PrepError, match="more than one guidance part"):
        parse_arm("guidance:overlay+guidance:none+skill:skills/lint")


@pytest.mark.parametrize("profile, overlay", [("claude-code", "claude"), ("codex", "codex"),
                                              ("codex-review", "codex"), ("copilot", "copilot"),
                                              ("copilot-review", "copilot")])
def test_builtin_profiles_name_the_overlay_of_their_agent(profile, overlay):
    assert load_profile(profile).overlay == overlay


def test_overlay_key_is_a_lowercase_agent_name():
    raw = yaml.safe_load((PROFILE_DIR / "codex.yaml").read_text(encoding="utf-8"))
    assert parse_profile(raw).overlay == "codex"
    with pytest.raises(ProfileError):
        parse_profile({**raw, "overlay": "Codex/../x"})
    with pytest.raises(ProfileError):  # the schema's `$` takes a trailing newline; the parser must not
        parse_profile({**raw, "overlay": "codex\n"})


def test_overlay_arm_keeps_base_and_the_harness_overlay(repo):
    meta, ws = _prep(repo, "guidance:overlay", load_profile("claude-code"))
    assert meta["arm"] == "guidance:overlay"
    assert (ws / "AGENTS.md").read_text() == "# guidance\n"
    assert (ws / "AGENTS.claude.md").read_text() == "# for claude\n"
    assert (ws / "sub/AGENTS.md").is_file()
    assert not (ws / "AGENTS.codex.md").exists() and not (ws / "sub/AGENTS.copilot.md").exists()
    assert meta["arm_files"] == ["AGENTS.codex.md", "sub/AGENTS.copilot.md"]
    assert meta["arm_overlay"] == {"name": "claude", "kept": ["AGENTS.claude.md"]}


def test_overlay_arm_follows_the_profile_not_the_arm_name(repo):
    meta, ws = _prep(repo, "guidance:overlay", load_profile("codex"), name="rd-codex")
    assert (ws / "AGENTS.codex.md").is_file() and not (ws / "AGENTS.claude.md").exists()
    assert meta["arm_files"] == ["AGENTS.claude.md", "sub/AGENTS.copilot.md"]
    assert meta["arm_overlay"] == {"name": "codex", "kept": ["AGENTS.codex.md"]}


def test_raw_arm_keeps_every_overlay(repo):
    meta, ws = _prep(repo, "guidance:raw", load_profile("claude-code"), name="rd-raw")
    assert (ws / "AGENTS.codex.md").is_file() and (ws / "AGENTS.claude.md").is_file()
    assert "arm_overlay" not in meta


def test_overlay_arm_needs_a_profile_with_an_overlay_key(repo):
    with pytest.raises(PrepError, match="needs a harness profile with an `overlay` key"):
        _prep(repo, "guidance:overlay", None, name="rd-none")
    with pytest.raises(PrepError, match="needs a harness profile with an `overlay` key"):
        _prep(repo, "guidance:overlay", dataclasses.replace(load_profile("claude-code"), overlay=None),
              name="rd-no-key")


def test_overlay_arm_refuses_a_base_with_no_overlay_for_its_agent(repo):
    with pytest.raises(PrepError, match="has no AGENTS.gemini.md to keep"):
        _prep(repo, "guidance:overlay", dataclasses.replace(load_profile("claude-code"), overlay="gemini"),
              name="rd-gemini")


def test_probe_files_include_every_overlay_even_outside_the_globs(repo):
    """The manifest's globs are ``**/AGENTS.md`` and the like: an overlay is probed for its own reach."""
    tree = repo["tree"]
    files = probe_files(tree, load_manifest(tree, ".agent-eval/repo-eval.yaml"), load_profile("claude-code"))
    assert {"AGENTS.claude.md", "AGENTS.codex.md", "sub/AGENTS.copilot.md"} <= set(files)
    assert all(not f.startswith(".agent-eval/") for f in files)


def test_overlay_report_flags_a_foreign_overlay_that_loads():
    records = [
        {"file": "AGENTS.md", "scenario": "cwd=.", "class": "loaded"},
        {"file": "AGENTS.claude.md", "scenario": "cwd=.", "class": "loaded"},
        {"file": "AGENTS.codex.md", "scenario": "cwd=.", "class": "not_loaded"},
        {"file": "sub/AGENTS.copilot.md", "scenario": "cwd=sub", "class": "fetched"},
        {"file": "sub/AGENTS.copilot.md", "scenario": "cwd=.", "class": "error"},
    ]
    paths = ["AGENTS.md", "AGENTS.claude.md", "AGENTS.codex.md", "sub/AGENTS.copilot.md"]
    assert overlay_report(records, "claude", {p: () for p in paths}) == {
        "own": "claude", "own_files": ["AGENTS.claude.md"], "own_loaded": ["AGENTS.claude.md"],
        "foreign_files": ["AGENTS.codex.md", "sub/AGENTS.copilot.md"],
        "cross_loaded": {"sub/AGENTS.copilot.md": {"cwd=sub": "fetched"}},
    }


def test_a_harness_without_an_overlay_has_only_foreign_overlays():
    records = [{"file": "AGENTS.codex.md", "scenario": "cwd=.", "class": "truncated"}]
    report = overlay_report(records, None, {"AGENTS.md": (), "AGENTS.codex.md": ()})
    assert report["own_files"] == [] and report["cross_loaded"] == {"AGENTS.codex.md": {"cwd=.": "truncated"}}


def test_overlay_report_flags_a_foreign_overlay_reached_through_a_link():
    """The probe seeds the file the link names; the harness reads the link, so the report keys on the link name."""
    records = [{"file": "docs/codex.md", "scenario": "cwd=.", "class": "loaded"}]
    report = overlay_report(records, "claude", {"docs/codex.md": ("AGENTS.codex.md",)})
    assert report["foreign_files"] == ["AGENTS.codex.md"]
    assert report["cross_loaded"] == {"AGENTS.codex.md": {"cwd=.": "loaded"}}


def test_overlay_arm_refuses_a_base_link_into_an_overlay_it_removes(tmp_path):
    (tmp_path / "AGENTS.md").symlink_to("AGENTS.codex.md")
    (tmp_path / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md"], "claude")
    assert (tmp_path / "AGENTS.codex.md").exists()  # refused before anything is removed


def test_overlay_arm_keeps_a_base_link_whose_target_a_removed_link_also_names(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "shared.md").write_text("shared\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to("docs/shared.md")
    (tmp_path / "AGENTS.codex.md").symlink_to("docs/shared.md")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    removed, _ = _apply_overlay(tmp_path, ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md", "docs/shared.md"],
                                "claude")
    assert removed == ["AGENTS.codex.md"]
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "shared\n"


@pytest.mark.skipif(os.name != "nt", reason="a backslash in a link target separates names on Windows only")
def test_overlay_arm_refuses_a_backslash_base_link_through_a_removed_directory_link(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "base.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.codex.md").symlink_to("docs", target_is_directory=True)
    (tmp_path / "AGENTS.md").symlink_to("AGENTS.codex.md\\base.md")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md"], "claude")


def test_overlay_arm_refuses_a_base_link_through_a_directory_link(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    (tmp_path / "dir").symlink_to("real")
    (tmp_path / "AGENTS.md").symlink_to("dir/AGENTS.codex.md")
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, ["AGENTS.md", "dir", "real/AGENTS.codex.md", "AGENTS.claude.md"], "claude")
    assert (tmp_path / "real" / "AGENTS.codex.md").exists()


def test_overlay_arm_does_not_hang_on_a_symlink_loop_in_the_base(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a").symlink_to("b")
    (tmp_path / "docs" / "b").symlink_to("a")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    (tmp_path / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    removed, _ = _apply_overlay(tmp_path, ["AGENTS.codex.md", "AGENTS.claude.md", "docs/a", "docs/b"], "claude")
    assert removed == ["AGENTS.codex.md"]


def test_text_report_states_the_cross_load_as_a_finding():
    payload = {
        "harness": {"name": "claude-code", "version": "2.1.0"}, "model": "m",
        "summary": {"cost_usd": 0.0, "matrix": {}}, "files": [], "unresolved": [],
        "scenarios": [{"id": "cwd=.", "error": None, "prose": 0, "unknown": []}],
        "records": [{"file": "AGENTS.claude.md", "scenario": "cwd=.", "class": "not_loaded"}],
        "overlays": {"own": "claude", "own_files": ["AGENTS.claude.md"], "own_loaded": [],
                     "foreign_files": ["AGENTS.codex.md"], "cross_loaded": {"AGENTS.codex.md": {"cwd=.": "loaded"}}},
    }
    text = format_matrix(payload)
    assert ("finding: AGENTS.codex.md, an overlay for another agent, was loaded by claude-code in cwd=. (loaded)"
            in text)
    assert "note: no claude overlay (AGENTS.claude.md) was loaded in any scenario" in text


def _payload(records, own, links, native=frozenset(), scenarios=("cwd=.",)):
    """A text-report payload with the overlay part computed from ``records`` the way run_reach computes it."""
    files = [{"path": t, **({"aliases": list(a)} if a else {})} for t, a in links.items()]
    return {
        "harness": {"name": "claude-code", "version": "2.1.0"}, "model": "m",
        "summary": {"cost_usd": 0.0, "matrix": {}}, "files": files, "unresolved": [],
        "scenarios": [{"id": s, "error": None, "prose": 0, "unknown": []} for s in scenarios],
        "records": records, "overlays": overlay_report(records, own, links, native),
    }


@pytest.mark.parametrize("cls, note", [
    ("not_loaded", "note: no claude overlay (AGENTS.claude.md) was loaded in any scenario"),
    ("error", "note: could not determine whether the claude overlay (AGENTS.claude.md) was loaded"),
])
def test_text_report_says_not_loaded_only_for_a_usable_negative_record(cls, note):
    records = [{"file": "AGENTS.claude.md", "scenario": "cwd=.", "class": cls}]
    text = format_matrix(_payload(records, "claude", {"AGENTS.claude.md": ()}))
    assert note in text
    assert ("was loaded in any scenario" in text) == (cls == "not_loaded")


def test_text_report_does_not_claim_not_loaded_for_an_all_error_run():
    records = [{"file": "AGENTS.claude.md", "scenario": s, "class": "error"} for s in ("cwd=.", "read=sub")]
    text = format_matrix(_payload(records, "claude", {"AGENTS.claude.md": ()}, scenarios=("cwd=.", "read=sub")))
    assert "was loaded in any scenario" not in text
    assert "could not determine whether the claude overlay (AGENTS.claude.md) was loaded" in text


def test_text_report_keeps_the_ambiguous_load_out_of_the_own_overlay_note():
    """A loaded file that two own/foreign names could name is ambiguous: no own-overlay note is printed for it."""
    records = [{"file": "docs/x.md", "scenario": "cwd=.", "class": "loaded"}]
    payload = _payload(records, "claude", {"docs/x.md": ("AGENTS.claude.md", "AGENTS.codex.md")})
    assert payload["overlays"]["ambiguous"] == ["AGENTS.claude.md", "AGENTS.codex.md"]
    assert "note:" not in format_matrix(payload)


def test_overlay_of_reads_the_bundled_profiles_not_a_same_named_file_in_the_cwd(tmp_path, monkeypatch):
    """A file named like a bundled profile, in the working directory, is not parsed as that profile."""
    (tmp_path / "codex").write_text("not: [a profile\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    native_names.cache_clear()
    try:
        assert overlay_of("AGENTS.claude.md") == "claude"
        assert native_names()
    finally:
        native_names.cache_clear()


def test_overlay_report_leaves_a_link_load_unattributed_when_a_native_file_may_import_it():
    """CLAUDE.md can import docs/shared.md, so a load of that file is not a load of its AGENTS.codex.md alias."""
    records = [{"file": "docs/shared.md", "scenario": "cwd=.", "class": "loaded"}]
    links = {"CLAUDE.md": (), "docs/shared.md": ("AGENTS.codex.md",)}
    report = overlay_report(records, "claude", links, native={"CLAUDE.md"})
    assert report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.codex.md"]


def test_text_report_still_says_not_loaded_for_an_own_overlay_next_to_an_unrelated_ambiguous_load():
    records = [{"file": "AGENTS.claude.md", "scenario": "cwd=.", "class": "not_loaded"},
               {"file": "AGENTS.codex.md", "scenario": "cwd=.", "class": "loaded"}]
    payload = _payload(records, "claude", {"AGENTS.claude.md": (), "AGENTS.codex.md": ("CLAUDE.md",)},
                       native={"CLAUDE.md"})
    assert payload["overlays"]["ambiguous"] == ["AGENTS.codex.md", "CLAUDE.md"]
    assert "note: no claude overlay (AGENTS.claude.md) was loaded in any scenario" in format_matrix(payload)


def test_text_report_does_not_claim_not_loaded_from_a_refusal():
    """A scenario that answered with prose (a refusal) gives no usable negative record."""
    records = [{"file": "AGENTS.claude.md", "scenario": "read=sub", "class": "not_loaded"}]
    payload = _payload(records, "claude", {"AGENTS.claude.md": ()}, scenarios=("read=sub",))
    payload["scenarios"][0]["prose"] = 40
    text = format_matrix(payload)
    assert "was loaded in any scenario" not in text
    assert "could not determine whether the claude overlay (AGENTS.claude.md) was loaded" in text


def test_overlay_report_leaves_a_load_of_two_foreign_aliases_unattributed():
    """A harness reading one of two foreign alias names of docs/shared.md reads the same file under both."""
    records = [{"file": "docs/shared.md", "scenario": "cwd=.", "class": "loaded"}]
    links = {"docs/shared.md": ("AGENTS.codex.md", "AGENTS.copilot.md")}
    report = overlay_report(records, "claude", links, native={"CLAUDE.md", "AGENTS.md"})
    assert report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.codex.md", "AGENTS.copilot.md"]


def test_overlay_report_leaves_a_load_of_a_foreign_target_with_a_foreign_alias_unattributed():
    records = [{"file": "AGENTS.codex.md", "scenario": "cwd=.", "class": "loaded"}]
    report = overlay_report(records, "claude", {"AGENTS.codex.md": ("AGENTS.copilot.md",)})
    assert report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.codex.md", "AGENTS.copilot.md"]


@pytest.mark.parametrize("via", ["absolute", "dotdot", "dot-then-up", "chain"])
def test_overlay_arm_refuses_a_base_link_to_a_removed_overlay_however_it_is_written(tmp_path, via):
    (tmp_path / "sub").mkdir()
    (tmp_path / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    files = ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md"]
    if via == "chain":
        (tmp_path / "mid.md").symlink_to("AGENTS.codex.md")
        (tmp_path / "AGENTS.md").symlink_to("mid.md")
        files.append("mid.md")
    else:
        target = {"absolute": str(tmp_path / "AGENTS.codex.md"),
                  "dotdot": f"../{tmp_path.name}/AGENTS.codex.md",
                  "dot-then-up": "./sub/../AGENTS.codex.md"}[via]
        (tmp_path / "AGENTS.md").symlink_to(target)
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, files, "claude")
    assert (tmp_path / "AGENTS.codex.md").exists()


def test_overlay_arm_refuses_an_own_overlay_that_links_outside_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "guide.md").write_text("outside\n", encoding="utf-8")
    (ws / "AGENTS.md").write_text("base\n", encoding="utf-8")
    (ws / "AGENTS.claude.md").symlink_to("../guide.md")
    (ws / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    with pytest.raises(PrepError, match="does not resolve to a usable file"):
        _apply_overlay(ws, ["AGENTS.md", "AGENTS.claude.md", "AGENTS.codex.md"], "claude")
    assert (ws / "AGENTS.codex.md").exists()


def test_overlay_arm_refuses_a_base_link_through_a_removed_directory_link(tmp_path):
    """A removed overlay that is a directory link: the base link resolves through it, so it would dangle."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "base.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.codex.md").symlink_to("docs")
    (tmp_path / "AGENTS.md").symlink_to("AGENTS.codex.md/base.md")
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md", "docs/base.md"], "claude")
    assert (tmp_path / "AGENTS.codex.md").is_symlink()  # refused before anything is removed


@pytest.mark.parametrize("own_target", ["missing.md", "AGENTS.claude.md", "docs"])
def test_overlay_arm_refuses_an_own_overlay_that_supplies_no_file(tmp_path, own_target):
    (tmp_path / "docs").mkdir()
    (tmp_path / "AGENTS.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.claude.md").symlink_to(own_target)  # dangling, a cycle, a directory
    (tmp_path / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    with pytest.raises(PrepError, match="does not resolve to a usable file"):
        _apply_overlay(tmp_path, ["AGENTS.md", "AGENTS.claude.md", "AGENTS.codex.md", "docs"], "claude")
    assert (tmp_path / "AGENTS.codex.md").exists()


def test_overlay_arm_reads_the_workspace_after_setup_patches_not_the_index(tmp_path):
    """A setup patch applied with git apply changes the tree and not the index: the arm sees an added foreign
    overlay (removes it) and a deleted one (skips it), instead of failing on a stale index entry."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("base\n", encoding="utf-8")
    (ws / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    (ws / "AGENTS.copilot.md").write_text("copilot\n", encoding="utf-8")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "base")
    (ws / "AGENTS.copilot.md").unlink()  # a setup patch deleted it
    (ws / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")  # and added this one, unstaged
    env = _git_env(SNAPSHOT_AUTHOR)
    removed, _ = _apply_arm(ws, env, tmp_path, None, parse_arm(OVERLAY_ARM), None, "claude")
    assert removed == ["AGENTS.codex.md"]
    assert not (ws / "AGENTS.codex.md").exists() and (ws / "AGENTS.claude.md").exists()


def test_overlay_arm_does_not_unlink_through_a_setup_patch_that_made_a_directory_a_link(tmp_path):
    """A setup patch replaces the tracked directory sub/ with a link to a host directory. The index still lists
    sub/AGENTS.codex.md and it now resolves outside the workspace: the arm must refuse, not delete the host file."""
    ws = tmp_path / "ws"
    ws.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    (host / "AGENTS.codex.md").write_text("host\n", encoding="utf-8")
    (ws / "AGENTS.md").write_text("base\n", encoding="utf-8")
    (ws / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    git(ws, "init", "-q")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "base")
    shutil.rmtree(ws / "sub")  # the setup patch, applied without the index
    (ws / "sub").symlink_to(host, target_is_directory=True)
    env = _git_env(SNAPSHOT_AUTHOR)
    with pytest.raises(PrepError, match="resolves outside the workspace"):
        _apply_arm(ws, env, tmp_path, None, parse_arm(OVERLAY_ARM), None, "claude")
    assert (host / "AGENTS.codex.md").read_text(encoding="utf-8") == "host\n"


def test_overlay_arm_refuses_a_kept_overlay_under_a_removed_directory_link(tmp_path):
    """A setup patch replaces the indexed directory AGENTS.codex.md/ with a link to docs/. The stale index entry
    AGENTS.codex.md/AGENTS.claude.md still resolves to a file, but once the removed link goes it no longer exists."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    (tmp_path / "AGENTS.codex.md").symlink_to("docs", target_is_directory=True)
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    tracked = ["AGENTS.md", "AGENTS.codex.md", "AGENTS.claude.md", "AGENTS.codex.md/AGENTS.claude.md",
               "docs/AGENTS.claude.md"]
    with pytest.raises(PrepError, match="the base would dangle"):
        _apply_overlay(tmp_path, tracked, "claude")
    assert (tmp_path / "AGENTS.codex.md").is_symlink()  # refused before anything is removed


@pytest.mark.parametrize("alias", ["sub", "aa"])  # the alias sorts after the real name, or before it
def test_overlay_arm_removes_a_file_a_directory_alias_lists_twice_once(tmp_path, alias):
    """A setup patch replaces the indexed directory `alias` with a link to real/: the index still lists
    alias/AGENTS.codex.md next to real/AGENTS.codex.md, which is one file. It is removed once, not twice."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "AGENTS.codex.md").write_text("codex\n", encoding="utf-8")
    (tmp_path / alias).symlink_to("real", target_is_directory=True)
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    tracked = sorted(["AGENTS.claude.md", "real/AGENTS.codex.md", f"{alias}/AGENTS.codex.md"])
    removed, _ = _apply_overlay(tmp_path, tracked, "claude")
    assert len(removed) == 1
    assert not (tmp_path / "real" / "AGENTS.codex.md").exists()


@pytest.mark.parametrize("listed", [
    ["AGENTS.claude.md", "AGENTS.codex.md", "AGENTS.codex.md/AGENTS.copilot.md", "real/AGENTS.copilot.md"],
    ["AGENTS.claude.md", "AGENTS.codex.md", "real/AGENTS.copilot.md"],
])
def test_overlay_arm_removes_a_file_under_a_removed_directory_alias_before_the_alias(tmp_path, listed):
    """AGENTS.codex.md is a link to real/ and the index also lists a file under it: the file goes before the link,
    or its unlink finds no path. Either listing names the same file, so it is removed once."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "AGENTS.copilot.md").write_text("copilot\n", encoding="utf-8")
    (tmp_path / "AGENTS.codex.md").symlink_to("real", target_is_directory=True)
    (tmp_path / "AGENTS.claude.md").write_text("claude\n", encoding="utf-8")
    _apply_overlay(tmp_path, sorted(listed), "claude")
    assert not os.path.lexists(tmp_path / "AGENTS.codex.md")
    assert not (tmp_path / "real" / "AGENTS.copilot.md").exists()
    assert (tmp_path / "AGENTS.claude.md").is_file()


def test_overlay_report_leaves_a_load_of_a_shared_target_unattributed():
    """A harness that loads AGENTS.md reads the file its AGENTS.claude.md and AGENTS.codex.md aliases name too:
    neither alias is pinned as loaded, and the load is reported as ambiguous."""
    records = [{"file": "AGENTS.md", "scenario": "cwd=.", "class": "loaded"}]
    links = {"AGENTS.md": ("AGENTS.claude.md", "AGENTS.codex.md")}
    report = overlay_report(records, "claude", links, native={"AGENTS.md"})
    assert report["own_loaded"] == [] and report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.claude.md", "AGENTS.codex.md"]
    assert report["own_files"] == ["AGENTS.claude.md"] and report["foreign_files"] == ["AGENTS.codex.md"]


def test_overlay_report_does_not_pin_a_shared_target_read_through_a_native_alias():
    """CLAUDE.md and AGENTS.codex.md both link to docs/shared.md: a Claude load of it is CLAUDE.md, not the foreign alias."""
    records = [{"file": "docs/shared.md", "scenario": "cwd=.", "class": "loaded"}]
    links = {"docs/shared.md": ("CLAUDE.md", "AGENTS.codex.md")}
    report = overlay_report(records, "claude", links, native={"CLAUDE.md"})
    assert report["cross_loaded"] == {}
    assert "AGENTS.codex.md" in report["ambiguous"]


def test_overlay_report_leaves_a_load_of_an_own_and_a_foreign_alias_unattributed():
    records = [{"file": "docs/x.md", "scenario": "cwd=.", "class": "loaded"}]
    report = overlay_report(records, "claude", {"docs/x.md": ("AGENTS.claude.md", "AGENTS.codex.md")})
    assert report["own_loaded"] == [] and report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.claude.md", "AGENTS.codex.md"]


def test_an_overlay_cell_is_identified_by_the_overlay_it_keeps(tmp_path):
    """A resumed batch must not reuse an overlay cell that kept another agent's overlay; other cells keep their ids."""
    from dokimi.repos.runner import AgentOptions, ExecutionMismatch, check_batch_executions, execution_record

    harness = tmp_path / "harness"
    harness.write_bytes(b"harness\n")
    claude = load_profile("claude-code")
    codex = dataclasses.replace(claude, overlay="codex")

    def options(profile):
        return AgentOptions(profile=profile, model="m", harness_bin=harness)

    def cell_id(profile, arm):
        return execution_record(options(profile), arm)["id"]

    assert cell_id(claude, "guidance:overlay") != cell_id(codex, "guidance:overlay")
    assert cell_id(claude, "guidance:none") == cell_id(codex, "guidance:none")
    assert cell_id(claude, "guidance:overlay") == execution_record(options(claude), "guidance:overlay")["id"]

    batch = tmp_path / "batch"
    cell = batch / "task__guidance-overlay__m__r1"
    cell.mkdir(parents=True)
    (cell / "result.json").write_text(
        json.dumps({"execution": {"id": cell_id(claude, "guidance:overlay"), "tools": "all", "delegation": "native"}}),
        encoding="utf-8")
    check_batch_executions(batch, [options(claude)])  # the same execution: no refusal
    with pytest.raises(ExecutionMismatch, match="another execution"):
        check_batch_executions(batch, [options(codex)])
    plain = batch / "task__guidance-none__m__r1"
    plain.mkdir()
    (plain / "result.json").write_text(
        json.dumps({"execution": {"id": cell_id(claude, "guidance:none"), "tools": "all", "delegation": "native"}}),
        encoding="utf-8")
    check_batch_executions(batch, [options(claude)])  # a plain cell beside an overlay cell of one profile


def test_overlay_report_leaves_a_load_of_a_foreign_target_with_an_own_alias_unattributed():
    records = [{"file": "AGENTS.codex.md", "scenario": "cwd=.", "class": "loaded"}]
    report = overlay_report(records, "claude", {"AGENTS.codex.md": ("AGENTS.claude.md",)})
    assert report["cross_loaded"] == {} and report["own_loaded"] == []
    assert report["ambiguous"] == ["AGENTS.claude.md", "AGENTS.codex.md"]
