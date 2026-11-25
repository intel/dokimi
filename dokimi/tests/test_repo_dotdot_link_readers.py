# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The readers of an event path that learn the workspace's links follow a ``..`` after a link as the system does
(contract §4.1 and the security scan, #1173); called with no links they take it by name, as before (#1042)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dokimi.cli import build_parser
from dokimi.repos import load_manifest, reach as reach_module
from dokimi.repos.event_format import followed_path
from dokimi.repos.arms import skill_usage
from dokimi.repos.container import WORKSPACE
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.held_guidance import HeldGuidance
from dokimi.repos.reach import SeededFile, classify, run_reach
from dokimi.repos.runner import _add_arm_use
from dokimi.repos.security import _outside_workspace, scan_cell
from dokimi.repos.transcript import Transcript
from dokimi.repos.uptake import arm_uptake
from dokimi.repos.useful import first_useful
from dokimi.repos.useful_spec import Useful
from dokimi.repos.workspace import Task
from repo_eval_fixture import SETUP_PATCH, FakeRunner, fake_harness, git, make_repo, write
from test_repo_arms import setup as arms_setup, toy  # noqa: F401  # fixtures
from test_repo_reach import _commit_links, _profile, _repo
from test_repo_security import _cell


def _link(path: Path, target: str | Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this OS refuses an unprivileged symlink")


@pytest.fixture
def workspace(tmp_path):
    """``alias -> docs/nested`` and ``sub/alias -> <outside>/nested``, as in the issue; ``src`` a plain directory."""
    ws, outside = tmp_path / "workspace", tmp_path / "outside"
    for d in (ws / "docs/nested", ws / "src", ws / "sub", outside / "nested"):
        d.mkdir(parents=True)
    _link(ws / "alias", "docs/nested")
    _link(ws / "sub/alias", outside / "nested")
    return ws


@pytest.fixture
def resolve(workspace):
    return HeldGuidance(workspace, []).resolved


def _reads(*paths: str) -> list[dict[str, Any]]:
    return [{"kind": "read", "path": p, "rc": 0} for p in paths]


def test_followed_path_follows_the_directories_before_the_last_name_and_keeps_that_name(resolve):
    assert followed_path("alias/../AGENTS.md", resolve) == "docs/AGENTS.md"
    assert followed_path("alias/../../AGENTS.md", resolve) == "AGENTS.md"
    assert followed_path("sub/alias/../x", resolve) == "../outside/x"
    assert followed_path("alias/..", resolve) == "docs"
    assert followed_path("src/../AGENTS.md", resolve) == "AGENTS.md"


def test_followed_path_takes_a_path_with_no_dotdot_a_path_with_no_resolve_and_an_absolute_one_by_name(resolve):
    assert followed_path("./alias//AGENTS.md", resolve) == "alias/AGENTS.md"
    assert followed_path("alias/../AGENTS.md") == "AGENTS.md"
    assert followed_path("/w/alias/../AGENTS.md", resolve) == "/w/AGENTS.md"


def test_followed_path_climbs_out_when_the_links_cannot_be_followed():
    assert followed_path("gone/../x", lambda p: None) == "../x"
    assert followed_path("gone/..", lambda p: None) == ".."


@pytest.mark.parametrize("path, files, reads", [
    ("alias/../AGENTS.md", ["AGENTS.md"], 0),  # the system opens docs/AGENTS.md
    ("alias/../AGENTS.md", ["docs/AGENTS.md"], 1),
    ("alias/../../AGENTS.md", ["AGENTS.md"], 1),  # by name it climbs out of the workspace
])
def test_uptake_credits_the_file_a_read_through_a_link_opens(resolve, path, files, reads):
    assert arm_uptake(_reads(path), files, [], "/ws", resolve)["reads"] == reads


@pytest.mark.parametrize("path, used", [
    ("alias/../.claude/skills/x/SKILL.md", []),
    ("alias/../../.claude/skills/x/SKILL.md", ["x"]),
])
def test_skill_usage_credits_the_skill_a_read_through_a_link_opens(resolve, path, used):
    assert skill_usage(_reads(path), ["x"], None, resolve)["skills_used"] == used


@pytest.mark.parametrize("path, outside", [
    ("sub/alias/../x", True),  # the link leads out of the workspace: it writes <outside>/x
    ("alias/../x", False),
    ("src/../x", False),
    ("alias/../../x", True),  # by name it climbs out: a finding is not lost to a link
])
def test_an_edit_through_a_link_that_leaves_the_workspace_is_outside_it(resolve, path, outside):
    assert _outside_workspace(path, resolve) is outside


def test_an_edit_path_is_checked_by_name_alone_without_the_links(resolve):
    assert _outside_workspace("sub/alias/../x") is False
    assert _outside_workspace("src/../../x") is True


def _findings(cell: Path) -> list[str]:
    return [f["rule"] for f in scan_cell(cell)["findings"]]


def test_a_scan_follows_the_links_of_the_cells_workspace(tmp_path, workspace):
    edit = {"t": None, "kind": "edit", "path": "sub/alias/../x"}
    cell = _cell(tmp_path / "b", "t", "skill", 0, events=[edit])
    assert _findings(cell) == []  # no workspace/ kept: by name, as before
    (cell / "workspace").mkdir()
    _link(cell / "workspace/sub/alias", workspace.parent / "outside/nested")
    assert _findings(cell) == ["edit-outside-workspace"]


def test_a_scan_does_not_read_the_links_of_a_workspace_that_is_itself_a_link(tmp_path, workspace):
    cell = _cell(tmp_path / "b", "t", "skill", 0, events=[{"t": None, "kind": "edit", "path": "sub/alias/../x"}])
    _link(cell / "workspace", workspace)  # the tree it leads to has the outside link
    assert _findings(cell) == []


def test_a_scan_of_an_edit_through_a_link_inside_the_workspace_finds_nothing(tmp_path, workspace):
    cell = _cell(tmp_path / "b", "t", "skill", 0, events=[{"t": None, "kind": "edit", "path": "alias/../x"}])
    (cell / "workspace/docs/nested").mkdir(parents=True)
    _link(cell / "workspace/alias", "docs/nested")
    assert _findings(cell) == []


@pytest.mark.parametrize("path, hit", [("sub/alias/../x", False), ("alias/../../sub/x", True)])
def test_first_useful_matches_the_file_an_edit_through_a_link_writes(resolve, path, hit):
    useful = Useful("edit", globs=("sub/x",))
    events = [{"kind": "edit", "path": path, "rc": 0, "t": 1.0}]
    assert (first_useful(useful, events, resolve)["reason"] is None) is hit
    assert first_useful(useful, events)["reason"] == ("never" if hit else None)  # by name it is the other way round


@pytest.mark.parametrize("path, seeded, read", [
    ("alias/../AGENTS.md", "AGENTS.md", False),
    ("alias/../AGENTS.md", "docs/AGENTS.md", True),
    ("alias/../../AGENTS.md", "AGENTS.md", True),
])
def test_reach_credits_the_file_a_read_through_a_link_opens(resolve, path, seeded, read):
    transcript = Transcript(events=_reads(path))
    assert classify(SeededFile(seeded, "RC000001", "RC000002"), transcript, resolve)["read_by_tool"] is read


def test_run_reach_classifies_with_the_links_of_the_probe_workspace(tmp_path, monkeypatch):
    seen: list[Any] = []
    real = reach_module.classify

    def spy(seeded, transcript, resolve=None):
        seen.append(resolve)
        return real(seeded, transcript, resolve)

    monkeypatch.setattr(reach_module, "classify", spy)
    monkeypatch.setenv("FAKE_MODE", "ok")
    root = _repo(tmp_path)
    (root / "CLAUDE.md").unlink()
    (root / "sub/CLAUDE.md").unlink()
    tree = _commit_links(root, {"CLAUDE.md": "AGENTS.md"})
    run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"), profile=load_profile(_profile(tmp_path)),
              model="m", out_dir=tmp_path / "out", work_dir=tmp_path, salt="s", kinds=("root",))
    assert seen and all(callable(r) for r in seen)
    assert seen[0]("CLAUDE.md") == "AGENTS.md"  # the link the probe workspace had


def test_add_arm_use_passes_the_links_to_the_guidance_skill_and_the_uptake(resolve):
    meta = {"guidance_skill": {"name": "g"}}
    events = _reads("alias/../.claude/skills/g/SKILL.md", "alias/../../tools/fmt")
    by_links: dict[str, Any] = {}
    by_name: dict[str, Any] = {}
    for record, links in ((by_links, resolve), (by_name, None)):
        _add_arm_use(record, meta, Task("t", {}, Path()), None, (["tools/fmt"], []), events, links)
    assert (by_links["guidance_skill_loaded"], by_links["uptake"]["reads"]) == (False, 1)
    assert (by_name["guidance_skill_loaded"], by_name["uptake"]["reads"]) == (True, 0)


def _link_patch(path: str, target: str) -> str:
    return (f"diff --git a/{path} b/{path}\nnew file mode 120000\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n"
            f"+{target}\n\\ No newline at end of file\n")


KIT_PATCH = _link_patch("alias", "docs/nested") + """diff --git a/tools/fmt b/tools/fmt
new file mode 100755
--- /dev/null
+++ b/tools/fmt
@@ -0,0 +1,2 @@
+#!/bin/sh
+exec clang-format -i "$@"
"""


def _run(root, exe, profile, out, *arms: str, task: str) -> Path:
    argv = ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
            "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out), "--cache-root",
            str(out / "caches"), "--agent-network", "host", "--task", task, "--reps", "1",
            *[x for a in arms for x in ("--arm", a)]]
    args = build_parser().parse_args(argv)
    args.func(args)
    return next((out / "b1").glob(f"{task}__*/result.json"))


def _agent_uses(monkeypatch, *calls: tuple[str, str]) -> None:
    monkeypatch.setenv("FAKE_TOOL_USES", json.dumps([[name, {"file_path": f"{WORKSPACE}/{path}"}] for name, path in calls]))
    monkeypatch.setenv("FAKE_TOOL_RESULTS", "1")


@pytest.mark.parametrize("path, reads", [("alias/../tools/fmt", 0), ("alias/../../tools/fmt", 1)])
def test_a_run_counts_the_read_of_an_added_file_the_workspace_links_lead_to(
        arms_setup, monkeypatch, path, reads):  # noqa: F811
    s = arms_setup
    patch = s["tmp"] / "kit.diff"
    patch.write_text(KIT_PATCH)
    _agent_uses(monkeypatch, ("Read", path))
    result = _run(s["root"], s["exe"], s["profile"], s["out"], f"patch:{patch}", task="fix_add")
    assert json.loads(result.read_text())["uptake"]["reads"] == reads


@pytest.fixture
def linked_repo(tmp_path, monkeypatch):
    """The toy repository, a ``lint`` skill and a task that adds ``alias -> docs/nested`` and ``sub/alias ->
    ../docs/nested`` to its workspace, and whose first useful result is an edit of ``sub/x``."""
    root, _ = make_repo(tmp_path, {"fix_lint": {"family": "fix", "grader": "calc", "setup_patch": "setup.patch",
                                                "expected_skill": "lint", "prompt": "fix it.",
                                                "useful": {"edit": "sub/x"}}})
    write(root, ".agent-eval/skills/lint/SKILL.md", "---\nname: lint\ndescription: lint things\n---\nDo lint.\n")
    write(root, ".agent-eval/tasks/fix_lint/setup.patch", SETUP_PATCH + _link_patch("alias", "docs/nested")
          + _link_patch("sub/alias", "../docs/nested"))  # the links are in the task's workspace, not the base
    git(root, "add", "-A")
    git(root, "commit", "-qm", "links")
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return root, exe, profile, tmp_path / "runs"


@pytest.mark.parametrize("skill_read, edit, used, found", [
    ("alias/../.claude/skills/lint/SKILL.md", "sub/alias/../x", [], False),
    ("alias/../../.claude/skills/lint/SKILL.md", "alias/../../sub/x", ["lint"], True),
])
def test_a_run_follows_the_workspace_links_for_the_skill_it_read_and_the_file_it_edited(
        linked_repo, monkeypatch, skill_read, edit, used, found):
    _agent_uses(monkeypatch, ("Read", skill_read), ("Edit", edit))
    result = json.loads(_run(*linked_repo, "guidance:raw+skill:skills/lint", task="fix_lint").read_text())
    assert result["skills"]["skills_used"] == used
    assert (result["first_useful"]["steps"] is not None) is found
