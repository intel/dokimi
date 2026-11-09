# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo tasks authoring``: one authoring task per merged pull request (contract §4.8).

The pull request is a :class:`.authoring.Change` from ``base``, the merge base of its
``headRefOid`` with ``mergeCommit^1`` (as review replay takes it, :mod:`.merged`), to its
``headRefOid``, titled and described as the pull request is. ``public_at`` is its ``createdAt``.
A git failure while one pull request is built skips that pull request with the reason.
"""

from __future__ import annotations

from datetime import datetime

from ..manifest import RepoManifest
from .authoring import Change, Options, Outcome, kept, write
from .history import History
from .merged import Skip, base_of, check_merged
from .threads import PullRequest, ReplayError


def task_id(pr: PullRequest) -> str:
    return f"authoring_pr{pr.number}"


def build_all(h: History, manifest: RepoManifest, prs: list[PullRequest], opts: Options) -> list[Outcome]:
    if opts.fetch:
        h.fetch(sha for pr in prs for sha in (pr.merge, pr.head) if sha)
    return [build_one(h, manifest, pr, opts) for pr in prs]


def build_one(h: History, manifest: RepoManifest, pr: PullRequest, opts: Options) -> Outcome:
    existing = kept(opts, pr.number, task_id(pr))
    if existing is not None:
        return existing
    notes: list[str] = []
    try:
        outcome = write(h, manifest, change_of(h, pr, notes), opts)
    except (Skip, ReplayError) as exc:
        return Outcome(pr.number, "skipped", reason=str(exc), notes=notes)
    outcome.notes = notes
    return outcome


def change_of(h: History, pr: PullRequest, notes: list[str]) -> Change:
    """``pr`` as a change; :class:`.merged.Skip` when it gives none."""
    if pr.body is None:  # a replay file without the field: the prompt would be the title alone
        raise Skip("no body in the input: the query in the contract (§4.8) asks for body and createdAt")
    merge, head = check_merged(h, pr)
    base = base_of(h, merge, head)
    if base is None:
        raise Skip(f"its headRefOid {head[:12]} shares no history with the base branch")
    fields = {"source_pr": pr.number, **({"source_url": pr.url} if pr.url else {}), "merge_commit": merge,
              "head_commit": head}
    return Change(id=task_id(pr), number=pr.number, title=pr.title, body=pr.body or "", base=base, target=head,
                  public_at=_public_at(pr, notes), fields=fields)


def _public_at(pr: PullRequest, notes: list[str]) -> str | None:
    """The PR's ``createdAt`` as the input gives it; ``None``, with a note, when it is missing, not ISO 8601 or has
    no offset (GitHub's ``Z`` is one), since it is then not comparable with a commit time."""
    try:
        at = datetime.fromisoformat(pr.created)
    except ValueError:
        at = None
    if at is None or at.tzinfo is None:
        notes.append(f"no public_at: createdAt {pr.created!r} is not a time with an offset")
        return None
    return pr.created
