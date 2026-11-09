# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What every task built from a merged pull request shares: review replay (contract §4.6) and authoring (§4.8).

The pull request must name its ``mergeCommit`` and ``headRefOid``, both in the source, and must not
be merged by fast-forward. Its ``base`` is the merge base of a revision of the PR with
``mergeCommit^1``, the base branch as the PR merged. Files the guidance globs match, overlays and
files under ``eval_dir`` are left out of what the task applies: the arms delete or replace
guidance, and ``eval_dir`` is never in a workspace.
"""

from __future__ import annotations

from ..manifest import RepoManifest, match_glob
from ..overlays import overlay_of
from .history import History
from .threads import PullRequest


class Skip(Exception):
    """A pull request that gives no task; the message says why."""


def check_merged(h: History, pr: PullRequest) -> tuple[str, str]:
    """``(mergeCommit, headRefOid)`` of ``pr``; :class:`Skip` when either is missing from the input or the
    source, or the PR was merged by fast-forward."""
    if pr.merge is None:
        raise Skip("not merged: no mergeCommit in the input")
    if pr.head is None:
        raise Skip("no headRefOid in the input")
    for name, sha, hint in (("mergeCommit", pr.merge, "the base branch"),
                            ("headRefOid", pr.head, f"refs/pull/{pr.number}/head")):
        if not h.has(sha):
            raise Skip(f"its {name} {sha[:12]} is not in the source (fetch {hint})")
    if pr.merge == pr.head:  # mergeCommit^1 is then the PR's own commit, not the base branch
        raise Skip("merged by fast-forward: its mergeCommit is its head, so the base branch it merged into "
                   "is unknown")
    return pr.merge, pr.head


def base_of(h: History, merge: str, rev: str) -> str | None:
    """The merge base of ``rev`` with ``merge^1``; ``None`` when they share no history."""
    return h.merge_base(rev, f"{merge}^1")


def left_out(manifest: RepoManifest, path: str) -> bool:
    """Whether ``path`` is guidance (a glob match or an overlay) or under ``eval_dir``."""
    return (any(match_glob(g, path) for g in manifest.guidance_globs) or overlay_of(path) is not None
            or manifest.in_eval_dir(path))
