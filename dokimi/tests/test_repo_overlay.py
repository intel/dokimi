# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance:overlay arm and the overlay reach check (agentsmd/agents.md#185); offline, no LLM."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.harness_profile import PROFILE_DIR, ProfileError, load_profile, parse_profile
from dokimi.repos.overlays import overlay_of
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
        "overlays": {"own": "claude", "own_files": ["AGENTS.claude.md"], "own_loaded": [],
                     "foreign_files": ["AGENTS.codex.md"], "cross_loaded": {"AGENTS.codex.md": {"cwd=.": "loaded"}}},
    }
    text = format_matrix(payload)
    assert ("finding: AGENTS.codex.md, an overlay for another agent, was loaded by claude-code in cwd=. (loaded)"
            in text)
    assert "note: no claude overlay (AGENTS.claude.md) was loaded in any scenario" in text


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


def test_overlay_report_leaves_a_load_of_a_shared_target_unattributed():
    """A harness that loads AGENTS.md reads the file its AGENTS.claude.md and AGENTS.codex.md aliases name too:
    neither alias is pinned as loaded, and the load is reported as ambiguous."""
    records = [{"file": "AGENTS.md", "scenario": "cwd=.", "class": "loaded"}]
    links = {"AGENTS.md": ("AGENTS.claude.md", "AGENTS.codex.md")}
    report = overlay_report(records, "claude", links, native={"AGENTS.md"})
    assert report["own_loaded"] == [] and report["cross_loaded"] == {}
    assert report["ambiguous"] == ["AGENTS.claude.md", "AGENTS.codex.md"]
    assert report["own_files"] == ["AGENTS.claude.md"] and report["foreign_files"] == ["AGENTS.codex.md"]


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
