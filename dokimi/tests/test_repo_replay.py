# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo tasks replay``: ``dokimi:review_replay`` tasks from recorded review threads (contract §4.5).

The threads files here have the shape ``gh api graphql --paginate --slurp`` writes, with the
SHAs of a toy history built per test. No network, no docker.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import dokimi.repos.replay.threads as threads_module
from dokimi.cli import build_parser
from dokimi.repos import RepoSource, load_manifest, open_source
from dokimi.repos.replay import History, ReplayError, load_pull_requests
from dokimi.repos.replay.history import map_line, near
from dokimi.repos.replay.threads import gold_problem
from dokimi.repos.workspace import export_eval_dir, load_tasks, prepare_workspace, source_git_dir
from repo_eval_fixture import FakeRunner, git, make_repo, write

LONG = [f"v{i}" for i in range(1, 41)]
NOT_IN_SOURCE = "ab" * 20


def _read(root: Path, rel: str) -> list[str]:
    return (root / rel).read_text(encoding="utf-8").splitlines()


def _put(root: Path, rel: str, lines: list[str]) -> None:
    write(root, rel, "".join(f"{ln}\n" for ln in lines))


def _set(root: Path, rel: str, n: int, text: str) -> None:
    lines = _read(root, rel)
    lines[n - 1] = text
    _put(root, rel, lines)


def _commit(root: Path, msg: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)
    return git(root, "rev-parse", "HEAD")


def _squash(root: Path, main: str, branch: str) -> str:
    git(root, "checkout", "-q", main)
    git(root, "merge", "-q", "--squash", branch)
    return _commit(root, f"squash {branch}")


@pytest.fixture
def hist(tmp_path):
    """main and three merged PRs.

    PR 7 is reviewed at ``rev``, merges main in (3 lines on top of long.py, line 21 changed),
    renames moved.py, then its author changes line 10 (now 13), deletes gone.py and rewrites lines
    5-10 of the renamed file, so that one diff from ``rev`` to ``r2`` shows no rename. Last,
    pre-commit.ci changes line 40 (now 43) in ``r2``. PR 8 changes only AGENTS.md. PR 12 is force-pushed after its review
    (``o12`` → ``h12``). PR 14 is merged with a merge commit, not squashed. PR 15 changes a file
    with CRLF line ends.
    """
    root, _ = make_repo(tmp_path)
    git(root, "config", "core.autocrlf", "false")  # the CRLF file stays as written
    _put(root, "src/long.py", LONG)
    (root / "src/blob.bin").write_bytes(b"\x00\x01bin")
    (root / "src/nonl.py").write_bytes(b"n1\nn2")  # no newline at the end
    _put(root, "src/[x].py", [f"b{i}" for i in range(1, 11)])  # a pathspec glob would match src/x.py
    _put(root, "src/x.py", [f"x{i}" for i in range(1, 11)])
    (root / "src/w.bat").write_bytes(b"echo 1\r\necho 2\r\necho 3\r\n")
    _put(root, "src/gone.py", ["g1", "g2", "g3"])
    _put(root, "src/moved.py", [f"m{i}" for i in range(1, 11)])
    _put(root, "src/r.py", [f"r{i}" for i in range(1, 11)])
    c0 = _commit(root, "more code")
    main = git(root, "rev-parse", "--abbrev-ref", "HEAD")  # the host's init.defaultBranch
    git(root, "checkout", "-qb", "pr7")
    _set(root, "src/long.py", 10, "v10 pr")
    _set(root, "src/long.py", 30, "v30 pr")
    _put(root, "AGENTS.md", ["# guidance", "more"])
    _set(root, "src/gone.py", 1, "g1 pr")
    _set(root, "src/moved.py", 5, "m5 pr")
    write(root, ".agent-eval/notes.md", "notes\n")
    (root / "src/blob.bin").write_bytes(b"\x00\x02bin2")
    (root / "src/nonl.py").write_bytes(b"n1 pr\nn2")
    rev = _commit(root, "pr work")
    git(root, "checkout", "-q", main)
    _put(root, "src/long.py", ["top1", "top2", "top3", *_read(root, "src/long.py")])
    _set(root, "src/long.py", 24, "v21 main")
    main1 = _commit(root, "main moves")
    git(root, "checkout", "-q", "pr7")
    git(root, "merge", "-q", "--no-edit", main)
    git(root, "mv", "src/moved.py", "src/renamed.py")
    _commit(root, "rename")
    _set(root, "src/long.py", 13, "v10 fixed")
    (root / "src/gone.py").unlink()
    _put(root, "src/renamed.py", [*_read(root, "src/renamed.py")[:4], "m5 fixed", *(f"x{i}" for i in range(6, 11))])
    _set(root, "src/[x].py", 9, "b9 fixed")
    _set(root, "src/x.py", 3, "x3 fixed")
    _commit(root, "address review")
    _set(root, "src/long.py", 43, "v40 formatted")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "[pre-commit.ci] auto fixes",
        "--author", "pre-commit-ci[bot] <66853113+pre-commit-ci[bot]@users.noreply.github.com>")
    r2 = git(root, "rev-parse", "HEAD")
    merge7 = _squash(root, main, "pr7")
    git(root, "checkout", "-qb", "pr8")
    _put(root, "AGENTS.md", [*_read(root, "AGENTS.md"), "pr8"])
    rev8 = _commit(root, "guidance only")
    merge8 = _squash(root, main, "pr8")
    git(root, "checkout", "-qb", "pr12")
    _set(root, "src/r.py", 5, "r5 first")
    o12 = _commit(root, "first try")
    git(root, "reset", "-q", "--hard", "HEAD~1")
    _set(root, "src/r.py", 5, "r5 second")
    h12 = _commit(root, "rewritten")
    merge12 = _squash(root, main, "pr12")
    git(root, "checkout", "-qb", "pr14")
    _set(root, "src/r.py", 8, "r8 pr")
    rev14 = _commit(root, "merged with a merge commit")
    before14 = git(root, "rev-parse", main)
    git(root, "checkout", "-q", main)
    git(root, "merge", "-q", "--no-ff", "--no-edit", "pr14")
    merge14 = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-qb", "pr15")
    (root / "src/w.bat").write_bytes(b"echo 1\r\necho two\r\necho 3\r\n")
    rev15 = _commit(root, "a CRLF file")
    merge15 = _squash(root, main, "pr15")
    git(root, "update-ref", "refs/pull/7/head", r2)  # as on GitHub: the PR's commits are on no branch
    git(root, "branch", "-q", "-D", "pr7", "pr8", "pr12", "pr14", "pr15")
    return {"root": root, "c0": c0, "rev": rev, "main1": main1, "r2": r2, "merge7": merge7, "rev8": rev8,
            "merge8": merge8, "o12": o12, "h12": h12, "merge12": merge12, "rev14": rev14, "before14": before14,
            "merge14": merge14, "rev15": rev15, "merge15": merge15}


def _t(path, line, commit, who="bob", body="please check", *, side="RIGHT", subject="LINE", typename="User",
       replies=(), created="2026-10-01T00:00:00Z"):
    comments = [{"author": {"login": who, "__typename": typename}, "body": body, "createdAt": created,
                 "url": f"https://example.invalid/r/{path}#{line}", "originalCommit": {"oid": commit},
                 "commit": {"oid": commit}}]
    comments += [{"author": {"login": a, "__typename": "User"}, "body": b, "createdAt": "2026-10-02T00:00:00Z"}
                 for a, b in replies]
    return {"path": path, "line": None, "originalLine": line, "startLine": None, "originalStartLine": None,
            "diffSide": side, "subjectType": subject, "isOutdated": True, "isResolved": False,
            "comments": {"nodes": comments}}


def _pr(number, head, merge, threads, reviews=()):
    """``reviews``: ``(login, body)`` or ``(login, body, __typename)``."""
    return {"number": number, "title": f"Change\n{number}", "url": f"https://example.invalid/pull/{number}",
            "author": {"login": "alice", "__typename": "User"}, "baseRefOid": None, "headRefOid": head,
            "mergeCommit": {"oid": merge} if merge else None, "reviewThreads": {"nodes": threads},
            "reviews": {"nodes": [{"author": {"login": r[0], "__typename": r[2] if len(r) > 2 else "User"}, "body": r[1],
                                   "state": "COMMENTED"} for r in reviews]}}


def _pr7(h):
    rev, r2 = h["rev"], h["r2"]
    return _pr(7, r2, h["merge7"], [
        _t("src/long.py", 10, rev, replies=[("carol", "+1"), ("alice", "fixed")]),  # t1: the author changed it
        _t("src/long.py", 22, rev, replies=[("alice", "it's intended")]),  # t2: only main changed near it
        _t("AGENTS.md", 2, rev),                                           # t3: a guidance file
        _t("src/gone.py", 1, rev),                                         # t4: the author deleted the file
        _t("src/renamed.py", 5, rev),  # t5: GitHub names the file as it is now; renamed, then changed
        _t("src/long.py", 33, r2),                                         # t6: second round, line 30 at rev
        _t("src/long.py", 3, rev, "copilot-pull-request-reviewer", typename="Bot"),  # t7
        _t("src/long.py", 4, rev, "alice"),                                # t8: the author's own note
        _t("src/long.py", 5, rev, side="LEFT"),                            # t9
        _t("src/long.py", 1, rev, subject="FILE"),                         # t10
        _t("src/long.py", 6, NOT_IN_SOURCE),                               # t11
        _t("src/long.py", 999, rev),                                       # t12
        _t("src/long.py", 13, r2),                                         # t13: at rev that line is the PR's own
        _t(".agent-eval/notes.md", 1, rev),                                # t14: under eval_dir
        _t("src/never.py", 1, rev),                                        # t15: in no revision
        _t("src/long.py", 21, rev),                                        # t16: main's merge rewrote it
        _t("src/renamed.py", 3, r2),                                       # t17: second round, moved.py at rev
        _t("src/long.py", 38, rev),                                        # t18: only a bot changed near it
        _t("src/nonl.py", 2, rev),                                         # t19: the last line, no newline
        _t("src/[x].py", 3, rev),                                          # t20: src/x.py changed near line 3
    ], reviews=[("bob", "Looks fine overall.\nDetails inline."), ("alice", "thanks"), ("carol", ""),
                ("copilot-pull-request-reviewer", "Summary of the changes", "Bot")])


def _merge_of_main(h) -> str:
    """The merge of main into PR 7: r2 <- the author's fixes <- rename <- merge."""
    hs = History(h["root"] / ".git")
    return hs.parents(hs.parents(hs.parents(h["r2"])[0])[0])[0]


def _pages(*prs):
    """``--paginate --slurp``: a list of pages; the last one repeats the first pull request."""
    return [{"data": {"search": {"pageInfo": {"hasNextPage": True, "endCursor": "x"}, "nodes": list(prs)}}},
            {"data": {"search": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(prs[:1])}}}]


def _replay(root: Path, doc, out: Path, *extra: str, source: str | None = None) -> None:
    path = root.parent / "threads.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    args = build_parser().parse_args(["repo", "tasks", "replay", source or str(root), "--threads", str(path),
                                      "--out", str(out), *extra])
    args.func(args)


def _load(out: Path, number: int) -> tuple[dict, list[dict], str]:
    d = out / f"review_replay_pr{number}"
    return (json.loads((d / "task.json").read_text()), json.loads((d / "gold.json").read_text()),
            (d / "change.patch").read_text())


# ------------------------------------------------------------------ the task of one PR


def test_a_merged_pr_becomes_a_task_with_gold_from_the_reviewers_line_threads(hist, tmp_path, capsys):
    out = tmp_path / "tasks"
    _replay(hist["root"], _pages(_pr7(hist)), out)
    task, gold, patch = _load(out, 7)
    assert [(g["id"], g["file"], g["line"], g["addressed"]) for g in gold] == [
        ("pr7-t1", "src/long.py", 10, True), ("pr7-t2", "src/long.py", 22, False),
        ("pr7-t4", "src/gone.py", 1, True), ("pr7-t5", "src/moved.py", 5, True),
        ("pr7-t6", "src/long.py", 30, False), ("pr7-t16", "src/long.py", 21, False),
        ("pr7-t17", "src/moved.py", 3, False), ("pr7-t18", "src/long.py", 38, False),
        ("pr7-t19", "src/nonl.py", 2, False), ("pr7-t20", "src/[x].py", 3, False)]
    assert gold[0]["reply"] == "fixed" and gold[1]["reply"] == "it's intended" and gold[3]["reply"] is None
    assert gold[0]["kind"] == "unknown" and gold[0]["comment"] == "please check"
    assert gold[0]["url"] == "https://example.invalid/r/src/long.py#10"
    assert task["base"] == hist["c0"] and task["reviewed_commit"] == hist["rev"]
    assert task["merge_commit"] == hist["merge7"] and task["source_pr"] == 7
    assert task["grader"] == "dokimi:review_replay" and task["score"] == {"metric": "recall", "max": 1.0}
    assert task["window"] == 5 and task["left_out_files"] == [".agent-eval/notes.md", "AGENTS.md"]
    assert "```json" in task["prompt"]
    assert "Subject: [PATCH] Change 7\n" in patch and "+v10 pr" in patch and "+v30 pr" in patch
    assert "AGENTS.md" not in patch and "notes.md" not in patch  # without the guidance and eval_dir hunks
    assert "v10 fixed" not in patch  # as reviewed
    printed = capsys.readouterr().out
    assert "PR 7: written review_replay_pr7: 10 gold thread(s), 3 addressed" in printed
    for note in ("review body by @bob: Looks fine overall.",
                 "pr7-t3 left out: on a guidance file or under eval_dir",
                 "pr7-t14 left out: on a guidance file or under eval_dir",
                 f"pr7-t15 left out: src/never.py is not in its revision {hist['rev'][:12]}",
                 "pr7-t7 left out: by a bot (@copilot-pull-request-reviewer)",
                 "pr7-t8 left out: opened by the PR author", "pr7-t9 left out: on a removed line",
                 "pr7-t10 left out: not on a line", f"pr7-t11 left out: its revision {NOT_IN_SOURCE} is not in",
                 "pr7-t12 left out: line 999 is not in src/long.py",
                 f"pr7-t13 left out: its line changed between {hist['r2'][:12]} and the reviewed revision",
                 f"pr7-t16: addressed=false: {_merge_of_main(hist)[:12]}, a merge or a bot's commit, rewrote its "
                 "line, so no later commit is read; check it"):
        assert note in printed, note
    assert "review body by @bob" in printed and printed.count("review body by") == 1  # not the author's,
    # an empty one or a bot's
    assert "1 task(s) written, 0 kept, 0 pull request(s) skipped" in printed


def test_addressed_counts_only_the_prs_own_commits_not_main_merged_in(hist, tmp_path):
    """t2: main changed line 21 and the PR merged main in. The diff from the reviewed revision to
    the merge commit has that change next to the thread; the PR's own commits do not."""
    _replay(hist["root"], _pages(_pr7(hist)), tmp_path / "tasks")
    _, gold, _ = _load(tmp_path / "tasks", 7)
    h = History(hist["root"] / ".git")
    assert near(22, h.hunks(hist["rev"], hist["merge7"], "src/long.py", "src/long.py"), 5)
    assert gold[1]["id"] == "pr7-t2" and gold[1]["addressed"] is False


def test_the_tasks_pass_the_self_check_and_apply_under_guidance_none(hist, tmp_path, monkeypatch):
    root = hist["root"]
    _replay(root, _pages(_pr7(hist)), root / ".agent-eval/tasks")
    _commit(root, "replay tasks")
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: FakeRunner())
    out = tmp_path / "chk"
    args = build_parser().parse_args(["repo", "tasks", "check", f"{root}@HEAD", "--out", str(out),
                                      "--cache-root", str(out / "caches"), "--task", "review_replay_pr7"])
    args.func(args)
    res = json.loads((out / "check/check.json").read_text())["tasks"][0]
    assert res["verdict"] == "ok" and res["oracle"]["score"] == 1.0 and res["null"]["score"] == 0.0

    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    task = load_tasks(export_eval_dir(tree, manifest, tmp_path / "content"), manifest, ["review_replay_pr7"])[0]
    for arm in ("guidance:none", "guidance:raw"):
        prepare_workspace(source_git_dir(source, tree), manifest, task, arm, tmp_path / arm)
        ws = tmp_path / arm / "workspace"
        assert _read(ws, "src/long.py")[9] == "v10 pr" and _read(ws, "src/moved.py")[4] == "m5 pr"
        assert (ws / "src/blob.bin").read_bytes() == b"\x00\x02bin2"  # a binary change applies
        assert (ws / "AGENTS.md").exists() == (arm == "guidance:raw")
        assert arm == "guidance:none" or _read(ws, "AGENTS.md") == ["# guidance"]  # the base's, not the PR's


# ------------------------------------------------------------------ the other pull requests


def test_prs_without_a_task_say_why_and_rewritten_history_is_flagged(hist, tmp_path, capsys):
    prs = [_pr(8, hist["rev8"], hist["merge8"], [_t("AGENTS.md", 3, hist["rev8"])]),
           _pr(9, hist["r2"], None, [_t("src/long.py", 10, hist["rev"])]),
           _pr(10, None, hist["merge7"], [_t("src/long.py", 10, hist["rev"])]),
           _pr(11, hist["r2"], hist["merge7"], []),
           _pr(12, hist["h12"], hist["merge12"], [_t("src/r.py", 5, hist["o12"])]),
           _pr(13, hist["r2"], NOT_IN_SOURCE, [_t("src/long.py", 10, hist["rev"])]),
           _pr(14, hist["rev14"], hist["merge14"], [_t("src/r.py", 8, hist["rev14"])]),
           _pr(16, hist["r2"], hist["merge7"], [_t("src/long.py", 10, hist["c0"])]),  # reviewed on main
           _pr(17, hist["r2"], hist["merge7"], [_t("src/long.py", 999, hist["rev"])])]  # its one thread drops
    out = tmp_path / "tasks"
    _replay(hist["root"], _pages(*prs), out)
    printed = capsys.readouterr().out
    for line in ("PR 8: skipped: it changes only guidance files or files under eval_dir",
                 "PR 9: skipped: not merged: no mergeCommit in the input",
                 "PR 10: skipped: no headRefOid in the input",
                 "PR 11: skipped: no review thread on a line by a reviewer",
                 f"PR 16: skipped: the reviewed revision {hist['c0'][:12]} changes nothing against the base branch",
                 "PR 17: skipped: no gold thread is left",
                 f"PR 13: skipped: its mergeCommit {NOT_IN_SOURCE[:12]} is not in the source "
                 "(fetch the base branch)",
                 "PR 12: written review_replay_pr12: 1 gold thread(s), 1 addressed",
                 "pr12-t1: addressed=true: no first-parent path leads from its revision to the PR head (history "
                 "rewritten, or the revision reached through a merge), so the diff to the head is read; check it"):
        assert line in printed, line
    task, gold, patch = _load(out, 12)
    assert gold[0]["addressed"] is True and task["reviewed_commit"] == hist["o12"] and "+r5 first" in patch
    task, gold, patch = _load(out, 14)  # the merge commit holds the reviewed revision: base is its first parent
    assert task["base"] == hist["before14"] and "+r8 pr" in patch and gold[0]["addressed"] is False
    assert sorted(p.name for p in out.iterdir()) == ["review_replay_pr12", "review_replay_pr14"]


def test_a_pr_merged_by_fast_forward_is_skipped_not_cut_to_its_last_commit(hist, tmp_path, capsys):
    """mergeCommit is the head, so mergeCommit^1 is the PR's first commit: the patch would lose it."""
    root = hist["root"]
    main = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    git(root, "checkout", "-qb", "pr30")
    _set(root, "src/r.py", 2, "r2 first commit")
    _commit(root, "one")
    _set(root, "src/r.py", 9, "r9 second commit")
    head = _commit(root, "two")
    git(root, "checkout", "-q", main)
    git(root, "merge", "-q", "--ff-only", "pr30")
    with pytest.raises(SystemExit):
        _replay(root, _pages(_pr(30, head, head, [_t("src/r.py", 2, head)])), tmp_path / "tasks")
    assert "PR 30: skipped: merged by fast-forward: its mergeCommit is its head" in capsys.readouterr().out
    assert not (tmp_path / "tasks/review_replay_pr30").exists()


def test_a_crlf_file_survives_git_am_in_a_base64_patch(hist, tmp_path, monkeypatch):
    root = hist["root"]
    _replay(root, _pages(_pr(15, hist["rev15"], hist["merge15"], [_t("src/w.bat", 2, hist["rev15"])])),
            root / ".agent-eval/tasks")
    _commit(root, "replay tasks")
    patch = (root / ".agent-eval/tasks/review_replay_pr15/change.patch").read_text()
    assert "Content-Transfer-Encoding: base64" in patch and "echo two" not in patch
    source = RepoSource.parse(f"{root}@HEAD")
    tree = open_source(source)
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    task = load_tasks(export_eval_dir(tree, manifest, tmp_path / "content"), manifest, ["review_replay_pr15"])[0]
    prepare_workspace(source_git_dir(source, tree), manifest, task, "guidance:none", tmp_path / "rd")
    assert (tmp_path / "rd/workspace/src/w.bat").read_bytes() == b"echo 1\r\necho two\r\necho 3\r\n"


def test_a_git_failure_skips_only_that_pull_request(hist, tmp_path, capsys, monkeypatch):
    lines = History.lines

    def failing(self, rev, path):
        if path == "src/r.py":
            raise ReplayError("git cat-file failed: fatal: simulated")
        return lines(self, rev, path)

    monkeypatch.setattr(History, "lines", failing)
    out = tmp_path / "tasks"
    _replay(hist["root"], _pages(_pr7(hist), _pr(14, hist["rev14"], hist["merge14"],
                                                  [_t("src/r.py", 8, hist["rev14"])])), out)
    printed = capsys.readouterr().out
    assert "PR 14: skipped: git cat-file failed: fatal: simulated" in printed
    assert "PR 7: written review_replay_pr7" in printed and (out / "review_replay_pr7/task.json").is_file()


def test_an_existing_task_is_kept_with_its_edits_unless_forced(hist, tmp_path, capsys):
    out = tmp_path / "tasks"
    _replay(hist["root"], _pages(_pr7(hist)), out)
    gold_path = out / "review_replay_pr7" / "gold.json"
    edited = [{**g, "kind": "correctness"} for g in json.loads(gold_path.read_text())]
    gold_path.write_text(json.dumps(edited))
    _replay(hist["root"], _pages(_pr7(hist)), out)
    assert json.loads(gold_path.read_text()) == edited
    assert "PR 7: kept review_replay_pr7: its task.json exists (--force rewrites it)" in capsys.readouterr().out
    _replay(hist["root"], _pages(_pr7(hist)), out, "--force", "--window", "2")
    assert {g["kind"] for g in json.loads(gold_path.read_text())} == {"unknown"}
    assert json.loads((out / "review_replay_pr7/task.json").read_text())["window"] == 2


def test_window_decides_how_near_a_later_change_addresses_a_thread(hist, tmp_path):
    """t1 at line 10 is changed exactly (distance 0); a thread on line 8 is 2 lines from that change."""
    threads = [_t("src/long.py", 8, hist["rev"])]
    for window, expected in (("2", True), ("1", False)):
        out = tmp_path / f"w{window}"
        _replay(hist["root"], _pages(_pr(7, hist["r2"], hist["merge7"], threads)), out, "--window", window)
        assert _load(out, 7)[1][0]["addressed"] is expected


@pytest.mark.parametrize("later, reviewed, base, lines", [
    ("r2", "r2", "main1", [33, 20]),   # rev's line 30 is r2's line 33; r2 merged main in, so its base is main's
    ("rev", "rev", "c0", [30, 17]),    # r2's line 20 is rev's line 17
])
def test_on_a_tie_the_revision_with_the_latest_comment_is_the_reviewed_one(hist, tmp_path, later, reviewed, base,
                                                                          lines):
    stamp = {k: "2026-10-05T00:00:00Z" if k == later else "2026-10-01T00:00:00Z" for k in ("rev", "r2")}
    threads = [_t("src/long.py", 30, hist["rev"], created=stamp["rev"]),
               _t("src/long.py", 20, hist["r2"], created=stamp["r2"])]
    _replay(hist["root"], _pages(_pr(7, hist["r2"], hist["merge7"], threads)), tmp_path / "tasks")
    task, gold, _ = _load(tmp_path / "tasks", 7)
    assert task["reviewed_commit"] == hist[reviewed] and task["base"] == hist[base]
    assert [g["line"] for g in gold] == lines


def test_the_revision_with_most_threads_is_reviewed_even_when_another_has_the_latest_comment(hist, tmp_path):
    early, late = "2026-10-01T00:00:00Z", "2026-10-05T00:00:00Z"
    threads = [*(_t("src/long.py", n, hist["rev"], created=early) for n in (30, 31, 32)),
               _t("src/long.py", 20, hist["r2"], created=late)]
    _replay(hist["root"], _pages(_pr(7, hist["r2"], hist["merge7"], threads)), tmp_path / "tasks")
    task, gold, _ = _load(tmp_path / "tasks", 7)
    assert task["reviewed_commit"] == hist["rev"] and task["base"] == hist["c0"]
    assert [g["line"] for g in gold] == [30, 31, 32, 17]


def test_a_file_edited_and_renamed_is_followed_one_commit_at_a_time(hist):
    """t5 and t17: one diff from rev to r2 has moved.py deleted, not renamed; commit by commit it is a rename."""
    h = History(hist["root"] / ".git")
    assert h.change(hist["rev"], hist["r2"], "src/moved.py") == "src/moved.py"  # deleted, as one diff sees it
    assert h.path_at(hist["rev"], "src/renamed.py", hist["r2"]) == "src/moved.py"
    assert h.path_at(hist["rev"], "src/never.py", hist["r2"]) is None


def test_the_walk_follows_the_first_parent_path_from_the_thread_revision_only(hist):
    h = History(hist["root"] / ".git")
    fixes = h.parents(hist["r2"])[0]
    rename = h.parents(fixes)[0]
    assert h.chain(hist["rev"], hist["r2"]) == [_merge_of_main(hist), rename, fixes, hist["r2"]]
    assert h.chain(hist["r2"], hist["r2"]) == []
    assert h.chain(hist["main1"], hist["r2"]) is None  # reached only through the merge's second parent
    assert h.chain(hist["o12"], hist["h12"]) is None  # not an ancestor: the PR was force-pushed


def test_a_thread_on_an_earlier_revision_is_carried_forward_through_a_rename(hist, tmp_path):
    """Two threads at r2 make it the reviewed revision; the one at rev is on moved.py, renamed.py at r2."""
    threads = [_t("src/renamed.py", 3, hist["rev"]), _t("src/long.py", 20, hist["r2"]),
               _t("src/long.py", 25, hist["r2"])]
    # --window 1: the author's fix of lines 5-10 would otherwise answer it before r2
    _replay(hist["root"], _pages(_pr(7, hist["r2"], hist["merge7"], threads)), tmp_path / "tasks", "--window", "1")
    task, gold, _ = _load(tmp_path / "tasks", 7)
    assert task["reviewed_commit"] == hist["r2"]
    assert [(g["file"], g["line"]) for g in gold] == [("src/renamed.py", 3), ("src/long.py", 20), ("src/long.py", 25)]


def test_a_thread_answered_before_the_reviewed_revision_is_left_out(hist, tmp_path, capsys):
    """t1 at ``a`` is answered by ``b`` (line 12), and ``b`` is the reviewed revision, so the patch
    holds the fix. t4 at the later ``c`` stays: ``c`` is not in the change under review."""
    root = hist["root"]
    main = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    git(root, "checkout", "-qb", "pr40")
    _set(root, "src/long.py", 10, "a10")
    _set(root, "src/long.py", 30, "a30")
    a = _commit(root, "a")
    _set(root, "src/long.py", 12, "guard added after review")
    b = _commit(root, "b")
    _set(root, "src/long.py", 37, "c37")
    c = _commit(root, "c")
    merge = _squash(root, main, "pr40")
    threads = [_t("src/long.py", 10, a, replies=[("alice", "fixed")]), _t("src/long.py", 30, b),
               _t("src/long.py", 31, b), _t("src/long.py", 35, c)]
    _replay(root, _pages(_pr(40, c, merge, threads)), tmp_path / "tasks")
    task, gold, patch = _load(tmp_path / "tasks", 40)
    assert task["reviewed_commit"] == b and "+guard added after review" in patch
    assert [(g["id"], g["line"]) for g in gold] == [("pr40-t2", 30), ("pr40-t3", 31), ("pr40-t4", 35)]
    assert f"pr40-t1 left out: answered before the reviewed revision {b[:12]}" in capsys.readouterr().out


def test_a_remote_source_fetches_the_prs_commits_by_sha(hist, tmp_path):
    """A clone holds the branches, not refs/pull/*: the reviewed revision and the PR's head are
    fetched from origin by SHA, and the task is the one a local source gives."""
    local, remote = tmp_path / "local", tmp_path / "remote"
    _replay(hist["root"], _pages(_pr7(hist)), local)
    _replay(hist["root"], _pages(_pr7(hist)), remote, "--cache-dir", str(tmp_path / "cache"),
            source=f"file://{hist['root']}@HEAD")
    assert _load(remote, 7) == _load(local, 7)


def test_a_prompt_file_replaces_the_default_prompt(hist, tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review HEAD as a dpnp maintainer.\n", encoding="utf-8")
    _replay(hist["root"], _pages(_pr7(hist)), tmp_path / "tasks", "--prompt-file", str(prompt))
    assert _load(tmp_path / "tasks", 7)[0]["prompt"] == "Review HEAD as a dpnp maintainer."


@pytest.mark.parametrize("content, message", [
    (None, "cannot read the threads file"),
    ("{not json", "cannot read the threads file"),
    ('{"data": {"search": {"nodes": []}}}', "the threads file holds no pull request"),
    ('[{"number": "7", "reviewThreads": []}]', "a pull request's number must be an integer, not '7'"),
    ('[{"number": 7, "reviewThreads": "x"}]', "pull request 7: reviewThreads is not a list of nodes"),
])
def test_an_unusable_threads_file_exits_1_with_the_reason(hist, tmp_path, capsys, content, message):
    path = tmp_path / "threads.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    args = build_parser().parse_args(["repo", "tasks", "replay", str(hist["root"]), "--threads", str(path),
                                      "--out", str(tmp_path / "tasks")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and message in capsys.readouterr().err
    assert not (tmp_path / "tasks").exists()


def test_a_threads_file_nested_past_the_parsers_limit_exits_1(hist, tmp_path, capsys, monkeypatch):
    """How deep ``json.loads`` goes depends on the interpreter (on 3.14, on the C stack), so the
    RecursionError is raised here rather than by a deep document."""
    def too_deep(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(threads_module, "json", SimpleNamespace(loads=too_deep))
    path = tmp_path / "threads.json"
    path.write_text("[[]]", encoding="utf-8")
    args = build_parser().parse_args(["repo", "tasks", "replay", str(hist["root"]), "--threads", str(path),
                                      "--out", str(tmp_path / "tasks")])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "cannot read the threads file" in capsys.readouterr().err
    assert not (tmp_path / "tasks").exists()


def test_no_task_from_any_pr_or_an_empty_prompt_file_exits_1(hist, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _replay(hist["root"], _pages(_pr(9, hist["r2"], None, [])), tmp_path / "tasks")
    assert exc.value.code == 1 and "✗ no pull request gave a task" in capsys.readouterr().err
    (tmp_path / "empty.md").write_text("\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _replay(hist["root"], _pages(_pr7(hist)), tmp_path / "tasks", "--prompt-file", str(tmp_path / "empty.md"))
    assert "is empty" in capsys.readouterr().err


# ------------------------------------------------------------------ reading the threads file


def test_the_threads_file_is_read_in_every_shape_gh_writes(hist):
    pr = _pr7(hist)
    one = {"data": {"repository": {"pullRequest": pr}}}
    for doc in (one, [pr], _pages(pr, _pr(8, None, None, []))):
        prs = load_pull_requests(doc)
        assert prs[0].number == 7 and len(prs[0].threads) == 20
    assert [p.number for p in load_pull_requests(_pages(_pr(8, None, None, []), pr))] == [7, 8]  # by number, once
    later = {**pr, "title": "edited later"}
    assert load_pull_requests([pr, later])[0].title == "Change\n7"  # the first copy is the one read
    t1 = load_pull_requests([pr])[0].threads[0]
    assert (t1.path, t1.line, t1.commit, t1.reviewer, t1.reply) == ("src/long.py", 10, hist["rev"], "bob", "fixed")


def test_a_value_that_is_not_a_full_sha_reaches_no_git_command(hist):
    pr = _pr(7, "--output=/tmp/x", "HEAD", [_t("src/long.py", 10, "HEAD~1")])
    got = load_pull_requests([pr])[0]
    assert got.head is None and got.merge is None and got.threads[0].commit is None
    assert gold_problem(got, got.threads[0]) == "no originalCommit in the input"
    upper = load_pull_requests([_pr(7, hist["r2"].upper(), None, [])])[0]
    assert upper.head == hist["r2"]


@pytest.mark.parametrize("value", ["a" * 40 + "^2", "a" * 40 + "~1", "a" * 41, "a" * 40 + "..HEAD", "b" * 64 + "^"])
def test_a_sha_with_anything_after_it_is_not_a_sha(value):
    got = load_pull_requests([_pr(7, value, value, [_t("src/long.py", 10, value)])])[0]
    assert got.head is None and got.merge is None and got.threads[0].commit is None


@pytest.mark.parametrize("change, reason", [
    ({"author": None}, "author unknown (a deleted account)"),
    ({"author": {"login": "renovate[bot]", "__typename": "User"}}, "by a bot (@renovate[bot])"),
    ({"author": {"login": "github-actions", "__typename": "Bot"}}, "by a bot (@github-actions)"),
    ({"author": {"login": "alice", "__typename": "User"}}, "opened by the PR author"),
])
def test_who_opened_a_thread_decides_whether_it_is_gold(change, reason):
    thread = _t("a.py", 1, "c" * 40)
    thread["comments"]["nodes"][0].update(change)
    pr = load_pull_requests([_pr(1, "d" * 40, "e" * 40, [thread])])[0]
    assert gold_problem(pr, pr.threads[0]) == reason


def test_a_thread_needs_a_comment_a_line_and_a_revision():
    no_comments = {**_t("a.py", 1, "c" * 40), "comments": {"nodes": []}}
    zero_line = _t("a.py", 0, "c" * 40)
    no_path = _t("", 3, "c" * 40)
    pr = load_pull_requests([_pr(1, "d" * 40, "e" * 40, [no_comments, zero_line, no_path, _t("a.py", 3, "c" * 40)])])[0]
    assert [gold_problem(pr, t) for t in pr.threads] == ["no comment", "not on a line", "not on a line", None]


def test_a_missing_file_is_a_replay_error_naming_it(tmp_path):
    from dokimi.repos.replay import read_threads

    with pytest.raises(ReplayError, match="cannot read the threads file .*nope.json"):
        read_threads(tmp_path / "nope.json")


# ------------------------------------------------------------------ line arithmetic


@pytest.mark.parametrize("line, hunks, expected", [
    (10, [], 10),
    (10, [(0, 0, 1, 3)], 13),          # 3 lines inserted at the top
    (10, [(10, 0, 11, 2)], 10),        # inserted after line 10: it stays
    (10, [(9, 0, 10, 2)], 12),         # inserted after line 9: it moves down
    (10, [(10, 1, 10, 1)], None),      # the line itself changed
    (10, [(8, 3, 8, 0)], None),        # removed with lines 8-10
    (10, [(7, 3, 7, 0)], 7),           # lines 7-9 removed above it
    (10, [(2, 1, 2, 4), (20, 1, 23, 1)], 13),  # a hunk below it does not move it
])
def test_map_line_carries_a_line_through_a_diff(line, hunks, expected):
    assert map_line(line, hunks) == expected


@pytest.mark.parametrize("hunks, window, expected", [
    ([(15, 1, 15, 1)], 5, True),
    ([(16, 1, 16, 1)], 5, False),
    ([(5, 2, 5, 0)], 5, True),         # removed lines 5-6: line 6 is 4 away from 10
    ([(3, 2, 3, 0)], 5, False),        # removed lines 3-4: line 4 is 6 away
    ([(4, 0, 5, 1)], 5, True),         # inserted between lines 4 and 5
    ([(3, 0, 4, 1)], 5, False),
])
def test_near_is_within_window_lines_of_a_change(hunks, window, expected):
    assert near(10, hunks, window) is expected
