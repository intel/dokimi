# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One ``dokimi:review_replay`` task per merged pull request (contract §4.6).

The reviewed revision is the ``originalCommit`` most gold threads were written on (on a tie,
the one with the latest comment). ``base`` is its merge base with the base branch as the
PR merged (``mergeCommit^1``), and ``change.patch`` is the diff from ``base`` to it as one
commit titled with the PR's title. A thread written on another revision is carried to the
reviewed one commit by commit, and left out when its line changed on the way.

Files the guidance globs match, and files under ``eval_dir``, are left out of the patch and
their threads out of the gold: the arms delete or replace guidance before the patch is
applied and ``eval_dir`` is never in a workspace, so a hunk on either would not apply.

A git failure while one pull request is built (a timeout, a blob a clone cannot fetch) skips
that pull request with the reason and leaves the others alone.
"""

from __future__ import annotations

import base64
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..manifest import RepoManifest, match_glob
from .history import History, addressed, carry
from .threads import PullRequest, ReplayError, Thread, gold_problem, review_bodies

PATCH = "change.patch"
GOLD = "gold.json"
DEFAULT_PROMPT = (
    "You are reviewing a pull request to this repository. The change under review is the HEAD commit "
    "(inspect it with `git show HEAD`). Review it as a maintainer of this project would: report every "
    "problem that should be fixed before merge (correctness, the project's guidelines and conventions, "
    "tests, documentation). Do not report things that are acceptable, and do not modify any files. End "
    "your answer with a fenced ```json block containing an array of findings, each an object "
    '{"file": "<repo-relative path>", "line": <line number in the new version of the file>, '
    '"severity": "blocker|major|minor", "rule": "<short rule name>", "message": "<one sentence>"}.'
)


@dataclass(frozen=True)
class Options:
    out: Path
    window: int = 5
    prompt: str = DEFAULT_PROMPT
    force: bool = False  # rewrite a task directory that already has a task.json
    fetch: bool = False  # fetch missing commits from origin (a cached clone; never a local repository)


@dataclass
class Outcome:
    """What became of one pull request: ``written``, ``kept`` (its task exists) or ``skipped``."""

    pr: int
    status: str
    task: str | None = None
    reason: str = ""
    n_gold: int = 0
    n_addressed: int = 0
    notes: list[str] = field(default_factory=list)  # threads left out, review bodies, `addressed` to check


class _Skip(Exception):
    """A pull request that gives no task; the message says why."""


def task_id(pr: PullRequest) -> str:
    return f"review_replay_pr{pr.number}"


def build_all(h: History, manifest: RepoManifest, prs: list[PullRequest], opts: Options) -> list[Outcome]:
    if opts.fetch:
        h.fetch(sha for pr in prs for sha in _needed(pr))
    return [build_one(h, manifest, pr, opts) for pr in prs]


def build_one(h: History, manifest: RepoManifest, pr: PullRequest, opts: Options) -> Outcome:
    dest = opts.out / task_id(pr)
    if (dest / "task.json").exists() and not opts.force:
        return Outcome(pr.number, "kept", task_id(pr), "its task.json exists (--force rewrites it)")
    notes = [f"review body by {b}" for b in review_bodies(pr)]
    try:
        task, gold, patch = _make(h, manifest, pr, opts, notes)
    except (_Skip, ReplayError) as exc:
        return Outcome(pr.number, "skipped", reason=str(exc), notes=notes)
    _write(dest, task, gold, patch)
    return Outcome(pr.number, "written", task_id(pr), n_gold=len(gold),
                   n_addressed=sum(g["addressed"] for g in gold), notes=notes)


def _make(h: History, manifest: RepoManifest, pr: PullRequest, opts: Options,
          notes: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    """``(task.json, gold.json, change.patch)`` of ``pr``; :class:`_Skip` when it gives no task."""
    _check_pr(h, pr)
    assert pr.merge is not None
    threads = _candidates(h, pr, notes)
    if not threads:
        raise _Skip("no review thread on a line by a reviewer")
    rev = _reviewed(threads)
    base = h.merge_base(rev, f"{pr.merge}^1")
    if base is None:
        raise _Skip(f"the reviewed revision {rev[:12]} shares no history with the base branch")
    changed = h.changed(base, rev)
    leave_out = sorted({p for p in changed if _left_out(manifest, p)})
    if not changed:
        raise _Skip(f"the reviewed revision {rev[:12]} changes nothing against the base branch")
    if set(changed) <= set(leave_out):
        raise _Skip("it changes only guidance files or files under eval_dir")
    gold = _gold(h, manifest, pr, threads, rev, opts.window, notes)
    if not gold:
        raise _Skip("no gold thread is left")
    return _task(pr, base, rev, leave_out, opts), gold, _mbox(pr, rev, h.patch(base, rev, leave_out))


def _needed(pr: PullRequest) -> list[str]:
    return [s for s in (pr.merge, pr.head, *(t.commit for t in pr.threads)) if s]


def _check_pr(h: History, pr: PullRequest) -> None:
    if pr.merge is None:
        raise _Skip("not merged: no mergeCommit in the input")
    if pr.head is None:
        raise _Skip("no headRefOid in the input")
    for name, sha, hint in (("mergeCommit", pr.merge, "the base branch"),
                            ("headRefOid", pr.head, f"refs/pull/{pr.number}/head")):
        if not h.has(sha):
            raise _Skip(f"its {name} {sha[:12]} is not in the source (fetch {hint})")


def _candidates(h: History, pr: PullRequest, notes: list[str]) -> list[Thread]:
    """The gold threads whose revision is in the source; the rest go to ``notes`` with the reason."""
    kept = []
    for t in pr.threads:
        why = gold_problem(pr, t)
        if why is None and t.commit and not h.has(t.commit):
            why = f"its revision {t.commit} is not in the source"
        if why:
            notes.append(f"{t.id} left out: {why} ({_where(t)})")
        else:
            kept.append(t)
    return kept


def _reviewed(threads: list[Thread]) -> str:
    """The revision most threads were written on; on a tie, the one with the latest comment."""
    count = Counter(t.commit for t in threads)
    latest = {t.commit: max(u.created for u in threads if u.commit == t.commit) for t in threads}
    return str(max(count, key=lambda c: (count[c], latest[c])))


def _gold(h: History, manifest: RepoManifest, pr: PullRequest, threads: list[Thread], rev: str,
          window: int, notes: list[str]) -> list[dict[str, Any]]:
    assert pr.head is not None
    gold = []
    for t in threads:
        assert t.commit is not None and t.line is not None  # gold_problem checked them
        path = h.path_at(t.commit, t.path, pr.head)
        why, at = (f"{t.path} is not in its revision {t.commit[:12]}", None) if path is None \
            else _anchor_problem(h, manifest, t.commit, path, t.line, rev)
        if why or at is None:
            notes.append(f"{t.id} left out: {why} ({_where(t)})")
            continue
        assert path is not None
        done, why = addressed(h, t.commit, path, t.line, pr.head, window)
        if why:
            notes.append(f"{t.id}: addressed={str(done).lower()}: {why}; check it")
        gold.append({"id": t.id, "file": at[0], "line": at[1], "kind": "unknown", "comment": t.comment,
                     "addressed": done, "reply": t.reply, "url": t.url})
    return gold


def _anchor_problem(h: History, manifest: RepoManifest, commit: str, path: str, line: int,
                    rev: str) -> tuple[str | None, tuple[str, int] | None]:
    """Why a thread on ``line`` of ``path`` at ``commit`` has no anchor at ``rev``, or its anchor."""
    at = carry(h, commit, rev, path, line)
    if at is None:
        return f"its line changed between {commit[:12]} and the reviewed revision {rev[:12]}", None
    if _left_out(manifest, at[0]):
        return "on a guidance file or under eval_dir, which the patch leaves out", None
    n_lines = h.lines(rev, at[0])
    if n_lines is None or at[1] > n_lines:
        return f"line {at[1]} is not in {at[0]} at the reviewed revision {rev[:12]}", None
    return None, at


def _left_out(manifest: RepoManifest, path: str) -> bool:
    return any(match_glob(g, path) for g in manifest.guidance_globs) or manifest.in_eval_dir(path)


def _where(t: Thread) -> str:
    return f"{t.path}:{t.line}" if t.line else (t.path or "no path")


def _task(pr: PullRequest, base: str, rev: str, leave_out: list[str], opts: Options) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id(pr), "family": "review", "base": base, "grader": "dokimi:review_replay",
        "score": {"metric": "recall", "max": 1.0}, "review_patch": PATCH, "gold": GOLD, "window": opts.window,
        "source_pr": pr.number, "reviewed_commit": rev, "merge_commit": pr.merge, "prompt": opts.prompt,
    }
    if pr.url:
        task["source_url"] = pr.url
    if leave_out:
        task["left_out_files"] = leave_out
    return task


def _mbox(pr: PullRequest, rev: str, diff: bytes) -> bytes:
    """``diff`` as one mail ``git am`` applies; the workspace resets its author (contract §3, step 7).

    ``git am`` strips the CR at the end of each line of a plain mail, so a diff with a CR in it (a
    file with CRLF line ends) goes base64-encoded, which keeps it; any other stays readable."""
    title = " ".join(pr.title.split()) or f"Pull request {pr.number}"
    body, encoding = b"---\n" + diff, "8bit"
    if b"\r" in diff:
        body, encoding = base64.encodebytes(body), "base64"
    head = (f"From {rev} Mon Sep 17 00:00:00 2001\nFrom: Reviewed Change <review@localhost>\n"
            "Date: Thu, 1 Jan 1970 00:00:00 +0000\nMIME-Version: 1.0\n"
            f"Content-Type: text/plain; charset=UTF-8\nContent-Transfer-Encoding: {encoding}\n"
            f"Subject: [PATCH] {title}\n\n")
    return head.encode("utf-8") + body


def _write(dest: Path, task: dict[str, Any], gold: list[dict[str, Any]], patch: bytes) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / PATCH).write_bytes(patch)
    (dest / GOLD).write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "task.json").write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
