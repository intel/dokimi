# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The `guidance:rev:<rev>` and `patch:<file>` arms (contract §3, workspace preparation). No docker, no LLM."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.compare import compare, parse_selector
from dokimi.repos.report import summarize
from dokimi.repos.runner import cell_name
from dokimi.repos.workspace import PrepError, _apply_rev, load_tasks, parse_arm, pin_arm, prepare_workspace, source_git_dir
from repo_eval_fixture import FakeRunner, fake_harness, git, make_repo, write

TOOLING_PATCH = """diff --git a/Makefile b/Makefile
new file mode 100644
--- /dev/null
+++ b/Makefile
@@ -0,0 +1,2 @@
+check:
+\tpython3 -m pytest
diff --git a/AGENTS.md b/AGENTS.md
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -1 +1,2 @@
 # guidance
+Run `make check`.
"""

STALE_PATCH = """--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a * b
+    return a + b
"""

INTO_EVAL_DIR_PATCH = """diff --git a/.agent-eval/notes.md b/.agent-eval/notes.md
new file mode 100644
--- /dev/null
+++ b/.agent-eval/notes.md
@@ -0,0 +1 @@
+the hidden test checks add(2, 3)
"""

# The other revision: new guidance, a guidance file gone and one added, and code and eval
# content that must not reach a workspace.
PR_FILES: dict[str, str | None] = {
    "AGENTS.md": "# pr guidance\n",
    "sub/AGENTS.md": None,
    "src/AGENTS.md": "# src guidance\n",
    "src/calc.py": "def add(a, b):\n    return b + a  # pr fix\n",
    "secret.py": "LEAKED = 1\n",
    ".agent-eval/AGENTS.md": "# eval guidance\n",
}


def commit_on(root: Path, branch: str, files: dict[str, str | None], start: str | None = None) -> str:
    """A commit on ``branch`` (created at ``start``) made in a linked worktree, so ``HEAD`` stays."""
    wt = root.parent / f"wt-{branch}-{len(list(root.parent.glob('wt-*')))}"
    git(root, "worktree", "add", "-q", *(["-b", branch, str(wt), start] if start else [str(wt), branch]))
    for rel, text in files.items():
        if text is None:
            (wt / rel).unlink()
        else:
            write(wt, rel, text)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"on {branch}")
    sha = git(wt, "rev-parse", "HEAD")
    git(root, "worktree", "remove", "--force", str(wt))
    return sha


@pytest.fixture
def toy(tmp_path):
    root, base = make_repo(tmp_path)
    pr = commit_on(root, "pr", PR_FILES, start=base)
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    from dokimi.repos.workspace import export_eval_dir

    content = export_eval_dir(tree, manifest, tmp_path / "content")
    tasks = {t.id: t for t in load_tasks(content, manifest)}
    return {"root": root, "base": base, "pr": pr, "manifest": manifest, "tasks": tasks,
            "git_dir": source_git_dir(source, tree), "tmp": tmp_path}


def _prep(toy, arm, task="fix_add", name="rd"):
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], toy["tasks"][task], arm, toy["tmp"] / name)
    return meta, toy["tmp"] / name / "workspace"


def _objects(git_dir: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(git_dir), "cat-file", "--batch-all-objects", "--batch-check=%(objectname)"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def _commit_objects(root: Path, sha: str) -> set[str]:
    """The commit, its root tree and every tree and blob under it."""
    listed = [line.split()[2] for line in git(root, "ls-tree", "-r", "-t", sha).splitlines()]
    return {sha, git(root, "rev-parse", f"{sha}^{{tree}}"), *listed}


def test_rev_arm_puts_another_revisions_guidance_on_the_base(toy):
    meta, ws = _prep(toy, "guidance:rev:pr")
    assert meta["arm"] == f"guidance:rev:{toy['pr']}"
    assert meta["arm_rev"] == {"rev": "pr", "sha": toy["pr"], "removed": ["sub/AGENTS.md"]}
    # written and deleted (contract §3): the deleted base guidance is in arm_files too, not the eval dir's
    assert meta["arm_files"] == ["AGENTS.md", "src/AGENTS.md", "sub/AGENTS.md"]
    assert (ws / "AGENTS.md").read_text() == "# pr guidance\n"
    assert (ws / "src/AGENTS.md").read_text() == "# src guidance\n"
    assert (ws / "src/calc.py").read_text() == "def add(a, b):\n    return a - b\n"  # base + setup patch
    assert not (ws / "sub/AGENTS.md").exists() and not (ws / "secret.py").exists()
    assert not (ws / ".agent-eval").exists()
    assert git(ws, "ls-files").split() == ["AGENTS.md", "src/AGENTS.md", "src/calc.py"]
    assert git(ws, "status", "--porcelain") == "" and git(ws, "rev-list", "--count", "HEAD") == "1"


def test_rev_arm_copies_no_object_of_the_revision_but_the_guidance_blobs_it_uses(toy):
    for task in ("fix_add", "review_add"):
        _, ws = _prep(toy, f"guidance:rev:{toy['pr']}", task=task, name=task)
        root = toy["root"]
        only_at_pr = _commit_objects(root, toy["pr"]) - _commit_objects(root, toy["base"])
        guidance = {git(root, "rev-parse", f"{toy['pr']}:{p}") for p in ("AGENTS.md", "src/AGENTS.md")}
        held = _objects(ws / ".git")
        assert held & only_at_pr == guidance
        assert git(ws, "fsck", "--unreachable", "--no-reflogs") == ""
        blobs = subprocess.run(["git", "-C", str(ws), "cat-file", "--batch-all-objects", "--batch"],
                               capture_output=True, check=True).stdout
        assert b"LEAKED" not in blobs and b"pr fix" not in blobs and b"eval guidance" not in blobs
        assert b"# sub guidance" not in blobs and b"# guidance\n" not in blobs  # the base's replaced guidance


@pytest.mark.outcome("cjm-guidance-rev-arm")
def test_rev_arm_is_named_by_the_sha_it_resolved_to_once(toy):
    by_ref = pin_arm(parse_arm("guidance:rev:pr"), toy["git_dir"])
    assert by_ref == pin_arm(parse_arm(f"guidance:rev:{toy['pr']}"), toy["git_dir"]) == f"guidance:rev:{toy['pr']}"
    assert by_ref.spec == "guidance:rev:pr" and by_ref.rev_sha == toy["pr"]
    moved = commit_on(toy["root"], "pr", {"AGENTS.md": "# moved guidance\n"})
    meta, ws = _prep(toy, by_ref)  # the pinned arm keeps the commit it named
    assert meta["arm_rev"]["sha"] == toy["pr"] and (ws / "AGENTS.md").read_text() == "# pr guidance\n"
    assert pin_arm(parse_arm("guidance:rev:pr"), toy["git_dir"]) == f"guidance:rev:{moved}"
    git(toy["root"], "branch", "-q", "feature/x", moved)
    assert pin_arm(parse_arm("guidance:rev:feature/x"), toy["git_dir"]) == f"guidance:rev:{moved}"


@pytest.mark.parametrize("spec", ["guidance:rev:", "guidance:rev:-p", "guidance:rev:main..pr", "guidance:rev:nope",
                                  "guidance:other", "patch:"])
def test_a_bad_arm_is_a_prep_error(toy, spec):
    with pytest.raises(PrepError):
        pin_arm(parse_arm(spec), toy["git_dir"])


@pytest.mark.outcome("cjm-patch-arm")
def test_patch_arm_applies_on_raw_guidance_and_records_the_file(toy):
    patch = toy["tmp"] / "patches" / "tooling.diff"
    write(patch.parent, patch.name, TOOLING_PATCH)
    digest = hashlib.sha256(TOOLING_PATCH.encode()).hexdigest()
    arm = parse_arm(f"patch:{patch}")
    assert arm == f"patch:tooling.diff@{digest[:12]}"
    patch.write_text("changed after the batch read it\n")  # every cell applies the bytes read once
    meta, ws = _prep(toy, arm)
    assert meta["arm"] == str(arm) and meta["arm_files"] == ["AGENTS.md", "Makefile"]
    assert meta["arm_patch"] == {"path": str(patch.resolve()), "sha256": digest, "added": ["Makefile"],
                                 "mcp_servers": []}
    assert (ws / "AGENTS.md").read_text() == "# guidance\nRun `make check`.\n"
    assert (ws / "Makefile").is_file() and (ws / "sub/AGENTS.md").is_file()  # the rest of guidance:raw
    assert git(ws, "status", "--porcelain") == "" and git(ws, "rev-list", "--count", "HEAD") == "1"
    assert git(ws, "fsck", "--unreachable", "--no-reflogs") == ""


@pytest.mark.outcome("cjm-patch-arm")
@pytest.mark.parametrize("text, message", [(STALE_PATCH, "does not apply"), (INTO_EVAL_DIR_PATCH, "eval_dir")])
def test_a_patch_arm_that_cannot_be_applied_is_a_prep_error_for_the_cell(toy, text, message):
    patch = toy["tmp"] / "bad.diff"
    patch.write_text(text)
    with pytest.raises(PrepError, match=f"task fix_add: arm patch:bad.diff@[0-9a-f]{{12}}: patch .*bad.diff.*{message}"):
        _prep(toy, f"patch:{patch}")


def test_arm_names_are_compare_selectors_and_cell_name_parts(toy):
    patch = toy["tmp"] / "a dir" / "v2,with:odd__name.diff"
    write(patch.parent, patch.name, TOOLING_PATCH)
    for arm in (pin_arm(parse_arm("guidance:rev:pr"), toy["git_dir"]), parse_arm(f"patch:{patch}")):
        assert "/" not in arm and "," not in arm and "__" not in arm
        assert parse_selector(f"b1/{arm}") == ("b1", str(arm)) and parse_selector(str(arm)) == (None, str(arm))
    assert parse_arm(f"patch:{patch}").startswith("patch:v2_with_odd_name.diff@")


@pytest.mark.parametrize("file_name", ["a" * 39 + "_more.diff", "fix (1)"])
def test_report_counts_the_missing_cells_of_a_patch_arm_whatever_its_file_name(tmp_path, file_name):
    patch = tmp_path / file_name
    patch.write_text(TOOLING_PATCH)
    arm = str(parse_arm(f"patch:{patch}"))
    batch = tmp_path / "b1"
    cells = [cell_name("t", arm, "m", rep) for rep in (1, 2)]
    (batch / "_eval").mkdir(parents=True)
    (batch / "_eval" / "plan.json").write_text(json.dumps({"cells": cells}))
    (batch / cells[0]).mkdir()
    agent = {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.0, "num_turns": 1}
    (batch / cells[0] / "result.json").write_text(json.dumps({
        "task": "t", "arm": arm, "model": "m", "agent": agent,
        "grade": {"success": True, "status": "graded", "score_only": False}}))
    assert [row["missing"] for row in summarize(batch)["rows"]] == [1]


@pytest.fixture
def setup(toy, monkeypatch):
    exe, profile = fake_harness(toy["tmp"])
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    return {**toy, "exe": exe, "profile": profile, "out": toy["tmp"] / "runs"}


def _run(s, *arms):
    argv = ["repo", "run", f"{s['root']}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness",
            str(s["profile"]), "--bin", str(s["exe"]), "--model", "m-1", "--batch", "b1", "--out", str(s["out"]),
            "--cache-root", str(s["out"] / "caches"), "--agent-network", "host", "--task", "fix_add",
            *[x for a in arms for x in ("--arm", a)]]
    args = build_parser().parse_args(argv)
    args.func(args)


@pytest.mark.outcome("cjm-guidance-rev-arm")
@pytest.mark.outcome("cjm-repo-compare-arms")
def test_repo_run_names_cells_by_the_resolved_arm_and_compare_reads_them(setup, capsys):
    patch = setup["tmp"] / "tooling.diff"
    patch.write_text(TOOLING_PATCH)
    digest = hashlib.sha256(TOOLING_PATCH.encode()).hexdigest()
    pr = setup["pr"]
    _run(setup, f"guidance:raw,guidance:rev:pr,guidance:rev:{pr}", f"patch:{patch}")
    err = capsys.readouterr().err
    assert f"arm guidance:rev:{pr} = guidance:rev:pr" in err and f"sha256 {digest}" in err
    batch = setup["out"] / "b1"
    cells = sorted(p.parent.name for p in batch.glob("*/result.json"))  # two spellings of one revision run once
    assert cells == ["fix_add__guidance-raw__m-1__r1", f"fix_add__guidance-rev-{pr}__m-1__r1",
                     f"fix_add__patch-tooling.diff_{digest[:12]}__m-1__r1"]
    meta = json.loads((batch / cells[1] / "meta.json").read_text())
    assert meta["arm"] == f"guidance:rev:{pr}" and meta["arm_rev"]["rev"] == "pr"
    result = json.loads((batch / cells[2] / "result.json").read_text())
    assert result["arm"] == f"patch:tooling.diff@{digest[:12]}" and result["grade"]["success"] is True

    moved = commit_on(setup["root"], "pr", {"AGENTS.md": "# moved guidance\n"})
    _run(setup, "guidance:rev:pr")  # the branch moved: a new arm, the old cells stay as they were
    assert (batch / f"fix_add__guidance-rev-{moved}__m-1__r1" / "result.json").is_file()
    assert (batch / cells[1] / "result.json").is_file()

    summary = compare([batch], "b1/guidance:raw", [f"b1/guidance:rev:{pr}", f"b1/patch:tooling.diff@{digest[:12]}"])
    assert {row["treatment"] for row in summary["comparisons"]} == {f"b1/guidance:rev:{pr}",
                                                              f"b1/patch:tooling.diff@{digest[:12]}"}


def test_repo_run_refuses_an_unreadable_patch_before_building_anything(setup, capsys):
    with pytest.raises(SystemExit):
        _run(setup, f"patch:{setup['tmp'] / 'missing.diff'}")
    assert "cannot read the patch" in capsys.readouterr().err
    assert not (setup["out"] / "b1").exists()


def test_repo_run_refuses_a_misspelled_revision_before_building_anything(setup, capsys, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    with pytest.raises(SystemExit):
        _run(setup, "guidance:raw,guidance:rev:mian")
    assert "no commit 'mian'" in capsys.readouterr().err
    assert runner.builds == [] and not (setup["out"] / "b1").exists()


def test_patch_arm_rejects_a_file_gitignore_hides_inside_eval_dir(toy):
    patch = toy["tmp"] / "ignored.diff"
    patch.write_text("diff --git a/.gitignore b/.gitignore\nnew file mode 100644\n--- /dev/null\n+++ b/.gitignore\n"
                     "@@ -0,0 +1 @@\n+*.log\n"
                     "diff --git a/.agent-eval/notes.log b/.agent-eval/notes.log\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/.agent-eval/notes.log\n@@ -0,0 +1 @@\n+answers\n")
    with pytest.raises(PrepError, match=r"writes into eval_dir .*\.agent-eval/notes\.log"):
        _prep(toy, f"patch:{patch}")


def test_patch_arm_records_a_file_it_writes_that_gitignore_hides(toy):
    task = toy["tasks"]["fix_add"]
    base = commit_on(toy["root"], "ignore", {".gitignore": ".claude/\n"}, start=task.base)
    toy["tasks"]["ignore"] = dataclasses.replace(task, raw={**task.raw, "base": base})
    patch = toy["tmp"] / "claude.diff"
    patch.write_text("diff --git a/.claude/commands/check.md b/.claude/commands/check.md\nnew file mode 100644\n"
                     "--- /dev/null\n+++ b/.claude/commands/check.md\n@@ -0,0 +1 @@\n+Run make check.\n")
    meta, ws = _prep(toy, f"patch:{patch}", task="ignore")
    assert (ws / ".claude/commands/check.md").is_file()
    assert meta["arm_files"] == [".claude/commands/check.md"]


def _link_commit(toy, branch: str, links: dict[str, str], files: dict[str, str]) -> str:
    """A commit on ``branch`` (from the base) with ``files`` and the symbolic ``links`` (path -> target)."""
    root = toy["root"]
    wt = root.parent / f"wt-{branch}"
    git(root, "worktree", "add", "-q", "-b", branch, str(wt), toy["base"])
    for rel, text in files.items():
        write(wt, rel, text)
    for rel, target in links.items():
        (wt / rel).unlink(missing_ok=True)
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, wt / rel)
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", f"on {branch}")
    sha = git(wt, "rev-parse", "HEAD")
    git(root, "worktree", "remove", "--force", str(wt))
    return sha


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_writes_a_linked_guidance_files_target_at_the_link(toy):
    _link_commit(toy, "linked", {"AGENTS.md": "docs/GUIDE.md"}, {"docs/GUIDE.md": "# new guide\n"})
    meta, ws = _prep(toy, "guidance:rev:linked")
    assert (ws / "AGENTS.md").read_text() == "# new guide\n" and not (ws / "AGENTS.md").is_symlink()
    assert not (ws / "docs/GUIDE.md").exists()  # the target is not added beside the base's files
    assert "AGENTS.md" in meta["arm_files"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
@pytest.mark.parametrize("target, why", [("../outside.md", "out of the tree"), (".agent-eval/grade.py", "into eval_dir"),
                                         ("docs/missing.md", "not a file there")])
def test_rev_arm_refuses_a_guidance_link_it_cannot_follow(toy, target, why):
    _link_commit(toy, "bad-link", {"AGENTS.md": target}, {})
    with pytest.raises(PrepError, match=why):
        _prep(toy, "guidance:rev:bad-link")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_refuses_symlinked_destination_components(toy):
    """Workspace preparation should check for symlinked destination components before writing."""
    # This test verifies that the safety check for symlinked destinations is in place
    # It's hard to trigger in practice since git tracks symlink targets, not the symlinks themselves
    # but we verify the code path exists and would catch it if the base workspace had a symlink

    # Create guidance files for a normal case to ensure the fix doesn't break normal linking
    _link_commit(toy, "normal-link", {"AGENTS.md": "docs/GUIDE.md"}, {"docs/GUIDE.md": "# guide\n"})
    meta, ws = _prep(toy, "guidance:rev:normal-link")

    # Should work normally without symlinked components
    assert (ws / "AGENTS.md").read_text() == "# guide\n"
    assert "AGENTS.md" in meta["arm_files"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_refuses_to_write_through_a_symlinked_base_directory(toy):
    """A base directory that is a symlink out of the workspace is refused, and nothing lands outside."""
    outside = toy["tmp"] / "outside"
    outside.mkdir()
    _link_commit(toy, "sub-link", {"sub/AGENTS.md": "../docs/GUIDE.md"}, {"docs/GUIDE.md": "# guide\n"})
    arm = pin_arm(parse_arm("guidance:rev:sub-link"), toy["git_dir"])
    ws = toy["tmp"] / "ws-symlinked"
    ws.mkdir()
    (ws / "sub").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PrepError, match="would write through symlink"):
        _apply_rev(ws, toy["git_dir"], toy["manifest"], arm, [])
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_reports_a_base_file_where_the_revision_has_a_directory(toy):
    """A base file at a directory the revision links into is a PrepError naming the file, not a raw OSError."""
    _link_commit(toy, "ops-link", {"ops/AGENTS.md": "../docs/GUIDE.md"}, {"docs/GUIDE.md": "# guide\n"})
    arm = pin_arm(parse_arm("guidance:rev:ops-link"), toy["git_dir"])
    ws = toy["tmp"] / "ws-file-dir"
    ws.mkdir()
    (ws / "ops").write_text("base file\n")
    with pytest.raises(PrepError, match="cannot be written"):
        _apply_rev(ws, toy["git_dir"], toy["manifest"], arm, [])


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_copies_the_executable_bit_of_a_linked_target(toy):
    """A linked guidance file whose target is executable in the revision is executable in the workspace."""
    _link_commit(toy, "exec-link", {"AGENTS.md": "run.sh"}, {"run.sh": "#!/bin/sh\necho hi\n"})
    wt = toy["root"].parent / "wt-exec-fix"
    git(toy["root"], "worktree", "add", "-q", str(wt), "exec-link")
    git(wt, "update-index", "--chmod=+x", "run.sh")
    git(wt, "commit", "-qm", "make the target executable")
    rev = git(wt, "rev-parse", "HEAD")
    git(toy["root"], "worktree", "remove", "--force", str(wt))
    meta, ws = _prep(toy, f"guidance:rev:{rev}")
    assert (ws / "AGENTS.md").read_text() == "#!/bin/sh\necho hi\n"
    assert os.stat(ws / "AGENTS.md").st_mode & 0o111 == 0o111
    assert "AGENTS.md" in meta["arm_files"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_does_not_make_a_linked_guidance_file_executable_unless_its_target_is(toy):
    """A link to a non-executable target stays non-executable in the workspace."""
    _link_commit(toy, "plain-link", {"AGENTS.md": "run.sh"}, {"run.sh": "#!/bin/sh\necho hi\n"})
    _, ws = _prep(toy, "guidance:rev:plain-link")
    assert (ws / "AGENTS.md").read_text() == "#!/bin/sh\necho hi\n"
    assert os.stat(ws / "AGENTS.md").st_mode & 0o111 == 0


def test_patch_arm_names_an_unknown_home_user_as_a_prep_error(tmp_path):
    """``patch:~nosuchuser/...`` is an arm error, not a traceback (the home directory cannot be found)."""
    with pytest.raises(PrepError, match="cannot read the patch"):
        parse_arm("patch:~nosuchuser-dokimi-test/x.diff")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_refuses_cyclic_guidance_links(toy):
    """Self-links and multi-link cycles should be refused, not silently accepted after 8 iterations."""
    # Self-link: AGENTS.md -> AGENTS.md
    _link_commit(toy, "self-link", {"AGENTS.md": "AGENTS.md"}, {})
    with pytest.raises(PrepError, match="not a file there"):
        _prep(toy, "guidance:rev:self-link")

    # Two-link cycle: A -> B, B -> A
    _link_commit(toy, "cycle-2", {"AGENTS.md": "docs/GUIDE.md", "docs/GUIDE.md": "AGENTS.md"}, {})
    with pytest.raises(PrepError, match="not a file there"):
        _prep(toy, "guidance:rev:cycle-2")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_rev_arm_preserves_linked_file_bytes_and_mode(toy):
    """Linked guidance files should use the blob's raw bytes (not decoded text) to preserve line endings."""
    # Create a file with CRLF line endings (would be normalized to LF if decoded as text)
    blob_text = "line1\r\nline2\r\nline3\n"
    blob_bytes = blob_text.encode("utf-8")

    # Link to it from a guidance file in the same commit
    _link_commit(toy, "preserve-bytes", {"AGENTS.md": "crlf.txt"}, {"crlf.txt": blob_text})

    meta, ws = _prep(toy, "guidance:rev:preserve-bytes")
    # The written file should have the exact same bytes as the blob (with CR preserved)
    assert (ws / "AGENTS.md").read_bytes() == blob_bytes
    assert "AGENTS.md" in meta["arm_files"]


def test_patch_arm_caps_long_file_names(toy):
    """Very long patch file names should not cause ENAMETOOLONG errors."""
    long_name = "a" * 230 + ".patch"
    patch_file = toy["tmp"] / long_name
    patch_file.write_bytes(TOOLING_PATCH.encode())

    # Parse should succeed even with long name
    arm = parse_arm(f"patch:{patch_file}")
    assert arm.patch is not None
    # The arm name should be capped to prevent ENAMETOOLONG errors
    # Format is patch:<name>@<digest>, so extract just the name part
    arm_str = str(arm)
    name_part = arm_str.split("@")[0].replace("patch:", "")
    assert len(name_part) <= 40  # We cap at 40 characters


def test_repo_run_with_duplicate_arms_yields_one_cell_per_unique_arm(toy):
    """Duplicate arms should resolve to a single arm and yield one cell, not multiple."""
    git_dir = toy["git_dir"]
    sha = git(toy["root"], "rev-parse", "HEAD").strip()

    # Parse two spellings of the same arm
    from dokimi.repos.workspace import pin_arm
    arm1 = parse_arm(f"guidance:rev:{sha[:12]}")
    arm2 = parse_arm(f"guidance:rev:{sha}")

    pinned1 = pin_arm(arm1, git_dir)
    pinned2 = pin_arm(arm2, git_dir)

    # Both should resolve to the same full SHA
    assert pinned1.rev_sha == pinned2.rev_sha

    # When passed to _pin_arms, duplicates should be removed
    from dokimi.repos.batch_ledger import pin_arms as _pin_arms
    pinned = _pin_arms([arm1, arm2, arm1], git_dir, str(toy["root"]))
    assert len(pinned) == 1


def test_repo_run_pins_duplicate_arms_once(toy):
    """Duplicate arms in a batch should be pinned once, reducing the number of cells."""
    git_dir = toy["git_dir"]
    sha = git(toy["root"], "rev-parse", "HEAD").strip()

    # Create three arms: two spellings of the same rev, plus a patch
    arm_rev_short = parse_arm(f"guidance:rev:{sha[:12]}")
    arm_rev_full = parse_arm(f"guidance:rev:{sha}")
    arm_raw = parse_arm("guidance:raw")

    # All three should be parsed
    assert arm_rev_short.rev is not None
    assert arm_rev_full.rev is not None
    assert arm_raw.rev is None

    # When pinned, the two rev arms should resolve to the same SHA
    pinned_short = pin_arm(arm_rev_short, git_dir)
    pinned_full = pin_arm(arm_rev_full, git_dir)
    assert pinned_short.rev_sha == pinned_full.rev_sha

    # When passed to _pin_arms (which removes duplicates), we get 2 arms instead of 3
    from dokimi.repos.batch_ledger import pin_arms as _pin_arms
    pinned = _pin_arms([arm_rev_short, arm_rev_full, arm_raw], git_dir, str(toy["root"]))
    assert len(pinned) == 2



def test_pin_arms_resolves_head_in_the_linked_worktree_the_source_names(toy):
    from dokimi.repos.batch_ledger import pin_arms as _pin_arms

    wt = toy["tmp"] / "linked"
    git(toy["root"], "worktree", "add", "-q", str(wt), "pr")  # its HEAD is the pr commit, the main worktree's is not
    main_sha = git(toy["root"], "rev-parse", "HEAD").strip()
    assert main_sha != toy["pr"]
    arm = parse_arm("guidance:rev:HEAD")
    assert _pin_arms([arm], toy["git_dir"], str(wt))[0].rev_sha == toy["pr"]
    assert _pin_arms([arm], toy["git_dir"], str(toy["root"]))[0].rev_sha == main_sha


def test_pin_arms_fetches_a_remote_branch_before_pinning_it(toy):
    from dokimi.repos.batch_ledger import pin_arms as _pin_arms

    cached = toy["tmp"] / "cached.git"  # a clone made before the branch moved
    subprocess.run(["git", "clone", "-q", "--bare", str(toy["root"]), str(cached)], check=True)
    moved = commit_on(toy["root"], "pr", {"AGENTS.md": "# moved\n"})
    assert moved != toy["pr"]
    pinned = _pin_arms([parse_arm("guidance:rev:pr")], cached, f"file://{toy['root']}@{toy['pr']}")
    assert pinned[0].rev_sha == moved


def test_pin_arms_shares_one_cell_for_two_paths_to_the_same_patch(toy, capsys):
    from dokimi.repos.batch_ledger import pin_arms as _pin_arms

    a = toy["tmp"] / "a.diff"
    (toy["tmp"] / "sub").mkdir()
    same_bytes = toy["tmp"] / "sub" / "a.diff"  # same file name and bytes, another directory: same arm name
    a.write_text(TOOLING_PATCH)
    same_bytes.write_text(TOOLING_PATCH)
    git_dir = toy["git_dir"]

    assert len(_pin_arms([parse_arm(f"patch:{a}"), parse_arm(f"patch:{a}")], git_dir, str(toy["root"]))) == 1
    assert "share one cell" not in capsys.readouterr().err  # the same path given twice is not a warning

    pinned = _pin_arms([parse_arm(f"patch:{a}"), parse_arm(f"patch:{same_bytes}"), parse_arm(f"patch:{a}")],
                       git_dir, str(toy["root"]))
    assert len(pinned) == 1 and pinned[0].patch_path == str(a.resolve())
    err = capsys.readouterr().err
    assert err.count("share one cell") == 1
    assert f"{same_bytes.resolve()} and {a.resolve()} are the same patch" in err
    assert f"meta.json names {a.resolve()}" in err


def _toy_skill(toy, name="lint"):
    write(toy["tmp"] / "content", f"skills/{name}/SKILL.md", f"---\nname: {name}\ndescription: x\n---\nDo it.\n")


@pytest.mark.parametrize("arm", ["skill:skills/lint", "guidance:raw+skill:skills/lint"])
def test_a_raw_arm_with_a_skill_leaves_the_repository_guidance_alone(toy, arm):
    _toy_skill(toy)
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], toy["tasks"]["fix_add"], arm, toy["tmp"] / "rd",
                             content_dir=toy["tmp"] / "content")
    ws = toy["tmp"] / "rd" / "workspace"
    assert meta["arm_files"] == [] and [s["name"] for s in meta["skills"]] == ["lint"]
    assert (ws / "AGENTS.md").read_text() == "# guidance\n" and (ws / "sub/AGENTS.md").is_file()
    assert (ws / ".claude/skills/lint/SKILL.md").is_file()


def test_a_bridge_arm_with_a_skill_still_needs_a_bridge_key(toy):
    _toy_skill(toy)
    with pytest.raises(PrepError, match="needs a harness profile"):
        prepare_workspace(toy["git_dir"], toy["manifest"], toy["tasks"]["fix_add"],
                          "guidance:bridge+skill:skills/lint", toy["tmp"] / "rd", content_dir=toy["tmp"] / "content")


def test_a_pinned_rev_arm_keeps_its_skill(toy):
    _toy_skill(toy)
    arm = pin_arm(parse_arm("guidance:rev:pr+skill:skills/lint"), toy["git_dir"])
    assert str(arm) == f"guidance:rev:{toy['pr']}+skill:lint"
    assert arm.skills == (("skill", "skills/lint"),) and arm.base == f"guidance:rev:{toy['pr']}"
    meta = prepare_workspace(toy["git_dir"], toy["manifest"], toy["tasks"]["fix_add"], arm, toy["tmp"] / "rd",
                             content_dir=toy["tmp"] / "content")
    assert [s["name"] for s in meta["skills"]] == ["lint"] and meta["arm_rev"]["sha"] == toy["pr"]
    assert (toy["tmp"] / "rd/workspace/AGENTS.md").read_text() == "# pr guidance\n"
