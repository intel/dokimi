# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance history a ``guidance:rev:<rev>`` arm records in ``meta.json`` ``arm_rev`` (contract §3).

Each commit of the toy history gets its own author and committer date, so the order by committer
date is not the order git walks in. No docker, no LLM.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import dokimi.repos.guidance_history as guidance_history
from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.workspace import export_eval_dir, load_tasks, prepare_workspace, source_git_dir
from repo_eval_fixture import git, make_repo, write

TOOLING_PATCH = """diff --git a/Makefile b/Makefile
new file mode 100644
--- /dev/null
+++ b/Makefile
@@ -0,0 +1 @@
+check:
"""


def _commit_at(root: Path, files: dict[str, str], subject: str, authored: str, committed: str) -> str:
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    env = {**os.environ, "GIT_AUTHOR_DATE": authored, "GIT_COMMITTER_DATE": committed}
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", subject],
                   check=True, capture_output=True, env=env)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def hist(tmp_path):
    """The toy repository's two commits, then: ``amended`` edits AGENTS.md (authored long before it was
    committed), ``code`` only src/calc.py, ``skewed`` sub/AGENTS.md with a committer date before its
    parent's, and ``added`` adds src/AGENTS.md. The arm's revision is ``added``."""
    root, _ = make_repo(tmp_path)
    shas = {
        "amended": _commit_at(root, {"AGENTS.md": "# guidance, from review\n"}, "Guidance for the fix in #12",
                              "2030-01-01T00:00:00+00:00", "2030-03-10T00:00:00+00:00"),
        "code": _commit_at(root, {"src/calc.py": "def add(a, b):\n    return a + b  # code\n"}, "code only",
                           "2030-03-11T00:00:00+00:00", "2030-03-11T00:00:00+00:00"),
        "skewed": _commit_at(root, {"sub/AGENTS.md": "# sub guidance 2\n"}, "sub guidance",
                             "2030-03-01T00:00:00+00:00", "2030-03-01T00:00:00+00:00"),
        "added": _commit_at(root, {"src/AGENTS.md": "# src guidance\n"}, "src guidance",
                            "2030-04-01T00:00:00+02:00", "2030-04-01T00:00:00+02:00"),
    }
    first = git(root, "rev-list", "--max-parents=0", "HEAD")  # make_repo's code commit: AGENTS.md, sub/AGENTS.md
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    tasks = {t.id: t for t in load_tasks(export_eval_dir(tree, manifest, tmp_path / "content"), manifest)}
    return {**shas, "first": first, "root": root, "manifest": manifest, "tasks": tasks,
            "git_dir": source_git_dir(source, tree), "tmp": tmp_path}


def _prep(h, arm, name="rd"):
    return prepare_workspace(h["git_dir"], h["manifest"], h["tasks"]["fix_add"], arm, h["tmp"] / name)


def _dates(h, name, authored, committed):
    return {"sha": h[name], "authored": authored, "committed": committed}


def test_a_rev_arm_records_the_latest_commit_of_each_guidance_file_with_both_dates(hist):
    rev = _prep(hist, f"guidance:rev:{hist['added']}")["arm_rev"]
    assert rev["guidance_latest"] == {
        "AGENTS.md": _dates(hist, "amended", "2030-01-01T00:00:00Z", "2030-03-10T00:00:00Z"),  # git's %aI, %cI
        "src/AGENTS.md": _dates(hist, "added", "2030-04-01T00:00:00+02:00", "2030-04-01T00:00:00+02:00"),
        "sub/AGENTS.md": _dates(hist, "skewed", "2030-03-01T00:00:00Z", "2030-03-01T00:00:00Z"),
    }
    assert rev["rev"] == rev["sha"] == hist["added"] and rev["removed"] == []


def test_a_files_latest_commit_is_chosen_by_committer_date_not_author_date(hist):
    backdated = _commit_at(hist["root"], {"sub/AGENTS.md": "# sub guidance 3\n"}, "newer author date",
                           "2031-01-01T00:00:00+00:00", "2030-02-01T00:00:00+00:00")
    rev = _prep(hist, f"guidance:rev:{backdated}")["arm_rev"]
    assert rev["guidance_latest"]["sub/AGENTS.md"]["sha"] == hist["skewed"]  # committed 2030-03-01
    assert [c["sha"] for c in rev["guidance_commits"]][2:4] == [hist["skewed"], backdated]


def test_guidance_commits_are_newest_first_by_committer_date_without_code_only_commits(hist):
    rev = _prep(hist, f"guidance:rev:{hist['added']}")["arm_rev"]
    # skewed descends from amended, so git lists it first; its committer date is older
    assert [(c["sha"], c["subject"]) for c in rev["guidance_commits"]] == [
        (hist["added"], "src guidance"), (hist["amended"], "Guidance for the fix in #12"),
        (hist["skewed"], "sub guidance"), (hist["first"], "code")]
    assert rev["guidance_commits"][1]["authored"] == "2030-01-01T00:00:00Z"
    assert rev["guidance_commits_truncated"] is False


def test_the_guidance_commits_cap_cuts_the_oldest_and_says_so(hist, monkeypatch):
    monkeypatch.setattr(guidance_history, "GUIDANCE_COMMITS_CAP", 2)
    rev = _prep(hist, f"guidance:rev:{hist['added']}")["arm_rev"]
    assert [c["sha"] for c in rev["guidance_commits"]] == [hist["added"], hist["amended"]]
    assert rev["guidance_commits_truncated"] is True
    assert set(rev["guidance_latest"]) == {"AGENTS.md", "src/AGENTS.md", "sub/AGENTS.md"}  # not capped
    monkeypatch.setattr(guidance_history, "GUIDANCE_COMMITS_CAP", 4)  # all four fit: nothing was cut
    rev = _prep(hist, f"guidance:rev:{hist['added']}", "at-cap")["arm_rev"]
    assert len(rev["guidance_commits"]) == 4 and rev["guidance_commits_truncated"] is False


def test_an_older_revision_records_only_its_own_history(hist):
    rev = _prep(hist, f"guidance:rev:{hist['amended']}")["arm_rev"]
    assert [c["sha"] for c in rev["guidance_commits"]] == [hist["amended"], hist["first"]]
    assert sorted(rev["guidance_latest"]) == ["AGENTS.md", "sub/AGENTS.md"]
    assert rev["guidance_latest"]["sub/AGENTS.md"]["sha"] == hist["first"]


def test_raw_and_patch_arms_record_no_guidance_history(hist):
    patch = hist["tmp"] / "tooling.diff"
    patch.write_text(TOOLING_PATCH)
    for arm, name in (("guidance:raw", "raw"), (f"patch:{patch}", "patch")):
        meta = _prep(hist, arm, name)
        assert "arm_rev" not in meta and not any(k.startswith("guidance_") for k in meta), arm
    assert _prep(hist, "guidance:rev:HEAD", "rev")["arm_rev"]["guidance_commits"]  # the rev arm does


def test_the_history_is_read_once_for_every_cell_of_the_arm(hist, monkeypatch):
    calls = []
    real = guidance_history._git_log
    monkeypatch.setattr(guidance_history, "_git_log", lambda *a: calls.append(a) or real(*a))
    first = _prep(hist, f"guidance:rev:{hist['added']}", "c1")["arm_rev"]
    second = _prep(hist, f"guidance:rev:{hist['added']}", "c2")["arm_rev"]
    assert calls == [(hist["git_dir"], hist["added"])]
    assert first == second and len(first["guidance_commits"]) == 4
