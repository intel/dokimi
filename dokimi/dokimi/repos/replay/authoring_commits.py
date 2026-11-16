# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval tasks generate --from history``: one authoring task per bug-fix commit (contract §4.8).

The commits are the first-parent, non-merge commits of the source's revision, newest first,
whose subject the ``--subject`` pattern matches; a revert, ``fixup!``, ``squash!`` or ``amend!``
commit is never one. Each is a :class:`.authoring.Change` from its
parent to itself, titled by its subject and described by the rest of its message, so the
repository needs no pull request file. A squash-merged pull request's subject ends with
``(#<n>)``: ``n`` is its number. ``public_at`` is the author date, the earliest time git records
for the change (a pull request was public from when it was opened, which git does not know).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..manifest import RepoManifest
from .authoring import Change, Options, Outcome, kept, task_of_pr, would_write, write
from .history import History
from .merged import Skip
from .threads import ReplayError

# Words a fix's subject uses (Exp4 on dokimi and oneDAL history: 87 of 267 and 43 of 1500 subjects).
DEFAULT_SUBJECT = (r"\bfix|\bbug|regress|crash|wrong|incorrect|broken|no longer|\bnever\b|\bhang|\bleak|off-by|"
                   r"mismatch|silently")
_PR_SUFFIX = re.compile(r"\s*\(#(\d+)\)\s*$")
# A revert ("Revert \"...", "revert: ...", "Revert fix for ...", "Back out ...") undoes a change: its oracle would undo
# the fix. fixup!/squash!/amend! commits are halves of another commit.
_NOT_A_FIX = re.compile(r"^(?:revert\b|back(?:ed)?[ -]out\b|fixup! |squash! |amend! )", re.I)
# The trailer lines that name people (with their e-mail) or are git's own, with their folded (indented) continuation
# lines: no part of a description.
_TRAILER = re.compile(r"^(?:(?:Signed-off|Co-authored|Reviewed|Acked|Tested|Reported|Suggested|Helped)-by|Cc|"
                      r"Change-Id): .*(?:\n[ \t]+\S.*)*$|^\(cherry picked from commit [0-9a-f]+\)$", re.I | re.M)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    authored: str  # %aI, strict ISO 8601 with an offset


def task_id(sha: str) -> str:
    return f"authoring_{sha[:12]}"


def first_parent(h: History, rev: str, n: int, subject: re.Pattern[str]) -> tuple[list[Commit], int, int]:
    """Of the newest ``n`` first-parent commits of ``rev``, the non-merge ones ``subject`` matches (no revert,
    ``fixup!``, ``squash!`` or ``amend!``): ``(matched, n non-merge commits read, n merges left out)``. The body loses its
    trailers: an e-mail in a ``Signed-off-by`` line is no part of the task."""
    log = h.first_parent_log(rev, n)
    commits = [Commit(sha, subj, without_trailers(body), at) for sha, n_parents, subj, at, body in log
               if n_parents < 2]
    matched = [c for c in commits if subject.search(c.subject) and not _NOT_A_FIX.match(c.subject)]
    return matched, len(commits), len(log) - len(commits)


def without_trailers(body: str) -> str:
    body = body.replace("\r\n", "\n")  # a CRLF message: \r is no part of a line ``$`` ends
    return re.sub(r"\n{3,}", "\n\n", _TRAILER.sub("", body)).strip()


def build_all(h: History, manifest: RepoManifest, commits: list[Commit], opts: Options, *,
              dry_run: bool = False) -> list[Outcome]:
    claimed: dict[int, str] = {}  # pull request -> the task this run gave it, so --dry-run sees what a run writes
    outcomes = []
    for c in commits:
        outcome = build_one(h, manifest, c, opts, dry_run=dry_run, claimed=claimed)
        if outcome.pr is not None and outcome.task and outcome.status in ("written", "would write"):
            claimed.setdefault(outcome.pr, outcome.task)
        outcomes.append(outcome)
    return outcomes


def build_one(h: History, manifest: RepoManifest, c: Commit, opts: Options, *, dry_run: bool = False,
              claimed: dict[int, str] | None = None) -> Outcome:
    existing = kept(opts, None, task_id(c.sha))
    if existing is not None:
        return existing
    m = _PR_SUFFIX.search(c.subject)
    number = int(m[1]) if m else None
    other = None
    if number is not None:
        other = (claimed or {}).get(number) or task_of_pr(opts.out, number)
    if other is not None and other != task_id(c.sha):  # one change is one task, whichever source wrote it
        return Outcome(number, "kept", other, f"{other} is this pull request's task")
    try:
        return (would_write if dry_run else write)(h, manifest, change_of(h, c), opts)
    except (Skip, ReplayError) as exc:
        return Outcome(None, "skipped", task_id(c.sha), reason=str(exc))


def change_of(h: History, c: Commit) -> Change:
    """``c`` as a change from its first parent; :class:`.merged.Skip` for a root commit."""
    parents = h.parents(c.sha)
    if not parents:
        raise Skip("a root commit has no parent to start from")
    m = _PR_SUFFIX.search(c.subject)
    number = int(m.group(1)) if m else None
    title = _PR_SUFFIX.sub("", c.subject) if m else c.subject
    fields = {"source_commit": c.sha, **({"source_pr": number} if number is not None else {})}
    return Change(id=task_id(c.sha), number=number, title=title, body=c.body, base=parents[0], target=c.sha,
                  public_at=c.authored or None, fields=fields)
