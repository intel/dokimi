# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance history a ``guidance:rev:<rev>`` arm records in ``meta.json`` ``arm_rev`` (contract §3).

Each commit of the toy history gets its own author and committer date, so the order by committer
date is not the order git walks in. No docker, no LLM.
"""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import dokimi.repos.guidance_history as guidance_history
from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.errors import PrepError
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


def test_cells_that_ask_for_one_history_at_once_read_it_once(hist, monkeypatch):
    """repo run prepares cells in a thread pool: the cells of an arm that start together share one read."""
    n, asked, all_asked, calls = 4, [], threading.Event(), []
    real_commits, real_log = guidance_history._guidance_commits, guidance_history._git_log

    def asking(*key):
        asked.append(key)
        if len(asked) == n:
            all_asked.set()
        return real_commits(*key)

    def log(*args):  # holds the first read until every cell has asked; without a lock, each cell reads
        calls.append(args)
        all_asked.wait(timeout=10)
        return real_log(*args)

    monkeypatch.setattr(guidance_history, "_guidance_commits", asking)
    monkeypatch.setattr(guidance_history, "_git_log", log)
    args = (hist["git_dir"], hist["manifest"].guidance_globs, hist["added"], ["AGENTS.md"], {})
    with ThreadPoolExecutor(n) as pool:
        results = list(pool.map(lambda _: guidance_history.arm_rev_history(*args), range(n)))
    assert all_asked.is_set() and calls == [(hist["git_dir"], hist["added"])]
    assert all(r == results[0] for r in results) and len(results[0]["guidance_commits"]) == 4


def test_a_subject_or_a_path_that_holds_a_separator_or_looks_like_a_header_stays_in_its_field(hist):
    """``-z`` framing: subjects and paths may hold \\x1e and \\x1f, a path may look like a commit id or a
    status record, and none of them starts a commit or ends one."""
    hexname = "0123456789abcdef0123456789abcdef01234567"
    code = _commit_at(hist["root"], {"src/calc.py": "# code\n", hexname: "x\n", ":M": "x\n"}, "code\x1eonly\x1fhere",
                      "2030-05-01T00:00:00+00:00", "2030-05-01T00:00:00+00:00")
    odd = _commit_at(hist["root"], {hexname: "y\n", "docs/a\x1eb/AGENTS.md": "# odd\n", f"sub/{hexname}/AGENTS.md": "#\n"},
                     "guidance\x1eedit\x1fnote", "2030-05-02T00:00:00+00:00", "2030-05-02T00:00:00+00:00")
    rev = _prep(hist, f"guidance:rev:{odd}")["arm_rev"]
    assert [(c["sha"], c["subject"]) for c in rev["guidance_commits"]] == [
        (odd, "guidance\x1eedit\x1fnote"), (hist["added"], "src guidance"),
        (hist["amended"], "Guidance for the fix in #12"), (hist["skewed"], "sub guidance"), (hist["first"], "code")]
    assert code not in {c["sha"] for c in rev["guidance_commits"]}
    assert rev["guidance_latest"]["docs/a\x1eb/AGENTS.md"] == rev["guidance_latest"][f"sub/{hexname}/AGENTS.md"] == {
        "sha": odd, "authored": "2030-05-02T00:00:00Z", "committed": "2030-05-02T00:00:00Z"}
    assert len(rev["guidance_latest"]) == 5


def test_a_log_field_that_should_start_a_commit_but_is_no_commit_id_is_a_prep_error():
    with pytest.raises(PrepError, match="'edit', not a commit id"):  # not a ValueError out of prepare_workspace
        guidance_history._parse_log("edit\x002030-01-01T00:00:00Z\x002030-01-01T00:00:00Z\x00guidance\x00")


def _branch_commit(root: Path, branch: str, files: dict[str, str], subject: str, date: str) -> str:
    """A commit on a new ``branch`` from HEAD; HEAD stays where it was."""
    git(root, "checkout", "-qb", branch)
    sha = _commit_at(root, files, subject, date, date)
    git(root, "checkout", "-q", "-")
    return sha


def _merge_at(root: Path, branch: str, files: dict[str, str], subject: str, authored: str, committed: str) -> str:
    """A merge of ``branch`` into HEAD, its result edited (or its conflict resolved) to hold ``files``."""
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "--no-commit", branch],
                   capture_output=True)  # a conflict exits 1: ``files`` resolves it
    return _commit_at(root, files, subject, authored, committed)


def test_a_merge_whose_resolution_edits_guidance_is_listed_with_its_committer_date(hist):
    theirs = _branch_commit(hist["root"], "theirs", {"AGENTS.md": "# theirs\n"}, "theirs", "2030-05-01T00:00:00+00:00")
    ours = _commit_at(hist["root"], {"AGENTS.md": "# ours\n"}, "ours", "2030-05-02T00:00:00+00:00",
                      "2030-05-02T00:00:00+00:00")
    merge = _merge_at(hist["root"], "theirs", {"AGENTS.md": "# resolved from review\n"}, "Merge theirs",
                      "2030-05-03T00:00:00+00:00", "2030-06-01T00:00:00+00:00")
    rev = _prep(hist, f"guidance:rev:{merge}")["arm_rev"]
    assert rev["guidance_latest"]["AGENTS.md"] == {
        "sha": merge, "authored": "2030-05-03T00:00:00Z", "committed": "2030-06-01T00:00:00Z"}
    assert [(c["sha"], c["subject"]) for c in rev["guidance_commits"]][:3] == [
        (merge, "Merge theirs"), (ours, "ours"), (theirs, "theirs")]
    assert rev["guidance_commits"][0]["committed"] == "2030-06-01T00:00:00Z"


def test_a_merge_that_takes_a_guidance_file_from_one_parent_is_not_listed(hist):
    feat = _branch_commit(hist["root"], "feat", {"sub/AGENTS.md": "# feat\n"}, "feat guidance",
                          "2030-05-01T00:00:00+00:00")
    code = _commit_at(hist["root"], {"src/calc.py": "# main\n"}, "main code", "2030-05-02T00:00:00+00:00",
                      "2030-05-02T00:00:00+00:00")
    merge = _merge_at(hist["root"], "feat", {}, "Merge feat", "2030-06-01T00:00:00+00:00", "2030-06-01T00:00:00+00:00")
    rev = _prep(hist, f"guidance:rev:{merge}")["arm_rev"]
    assert rev["guidance_latest"]["sub/AGENTS.md"]["sha"] == feat
    assert [c["sha"] for c in rev["guidance_commits"]][:2] == [feat, hist["added"]]
    assert not {merge, code} & {c["sha"] for c in rev["guidance_commits"]}


def test_a_clean_merge_of_edits_to_different_parts_of_one_guidance_file_is_listed(hist):
    """Its result differs from each parent, so git's dense combined listing names the file (contract §3)."""
    lines = [f"line {i}\n" for i in range(9)]
    base = _commit_at(hist["root"], {"AGENTS.md": "".join(lines)}, "lines", "2030-05-01T00:00:00+00:00",
                      "2030-05-01T00:00:00+00:00")
    top = _branch_commit(hist["root"], "top", {"AGENTS.md": "".join(["top\n", *lines[1:]])}, "top",
                         "2030-05-02T00:00:00+00:00")
    end = _commit_at(hist["root"], {"AGENTS.md": "".join([*lines, "end\n"])}, "end", "2030-05-03T00:00:00+00:00",
                     "2030-05-03T00:00:00+00:00")
    merge = _merge_at(hist["root"], "top", {}, "Merge top", "2030-06-01T00:00:00+00:00", "2030-06-01T00:00:00+00:00")
    assert (hist["root"] / "AGENTS.md").read_text() == "".join(["top\n", *lines[1:], "end\n"])  # clean, no edit
    rev = _prep(hist, f"guidance:rev:{merge}")["arm_rev"]
    assert [c["sha"] for c in rev["guidance_commits"]][:4] == [merge, end, top, base]


@pytest.mark.skipif(os.name == "nt", reason="symbolic links")
def test_an_edit_to_a_guidance_links_target_counts_for_the_link(hist):
    """The arm installs ``AGENTS.md -> docs/GUIDE.md`` as the target's bytes; no glob matches docs/GUIDE.md."""
    (hist["root"] / "AGENTS.md").unlink()
    os.symlink("docs/GUIDE.md", hist["root"] / "AGENTS.md")
    link = _commit_at(hist["root"], {"docs/GUIDE.md": "# guide\n"}, "link the guide", "2030-05-01T00:00:00+00:00",
                      "2030-05-01T00:00:00+00:00")
    edit = _commit_at(hist["root"], {"docs/GUIDE.md": "# guide, from review\n"}, "Guide for #12",
                      "2030-06-01T00:00:00+00:00", "2030-06-01T00:00:00+00:00")
    rev = _prep(hist, f"guidance:rev:{edit}")["arm_rev"]
    assert rev["guidance_latest"]["AGENTS.md"] == {
        "sha": edit, "authored": "2030-06-01T00:00:00Z", "committed": "2030-06-01T00:00:00Z"}
    assert [(c["sha"], c["subject"]) for c in rev["guidance_commits"]][:2] == [(edit, "Guide for #12"),
                                                                              (link, "link the guide")]
    assert "docs/GUIDE.md" not in rev["guidance_latest"]
