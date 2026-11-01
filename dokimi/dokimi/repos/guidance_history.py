# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance history of a ``guidance:rev:<rev>`` arm, for its ``meta.json`` ``arm_rev`` (contract §3).

A replay task's answer is public from its ``public_at`` (§4.6); a guidance commit made after it may
encode that review. :func:`arm_rev_history` records what a reader needs to check that: the commit
that last touched each guidance file at the revision, and the newest commits that touch a guidance
path, each with its author and committer date. The flag reads the committer date: ``git commit
--amend`` and a rebase keep the author date (https://github.com/intel/dokimi/issues/290).

The history is one ``git log`` over the revision, filtered with :func:`~.manifest.match_glob`, and
read once per git dir, revision and globs in a process: every cell of the arm asks the same.
``git log`` lists no files for a merge commit, so a merge is never listed: the commits it merged are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dokimi import process

from .errors import PrepError
from .manifest import match_glob

#: At most this many commits in ``guidance_commits``; ``guidance_latest`` is not capped
GUIDANCE_COMMITS_CAP = 200
_GIT_TIMEOUT_S = 1800.0
_FORMAT = "--format=%x1e%H%x1f%aI%x1f%cI%x1f%s"  # subject last: a \x1f in it stays in it


@dataclass(frozen=True)
class _Commit:
    sha: str
    authored: str
    committed: str
    subject: str
    paths: tuple[str, ...]  # the guidance paths it touched

    def dates(self) -> dict[str, str]:
        return {"sha": self.sha, "authored": self.authored, "committed": self.committed}


def arm_rev_history(git_dir: Path, globs: tuple[str, ...], sha: str, files: list[str]) -> dict[str, Any]:
    """``guidance_latest`` for each of ``files`` (the guidance at ``sha``), ``guidance_commits`` (newest
    first by committer date, at most :data:`GUIDANCE_COMMITS_CAP`) and ``guidance_commits_truncated``."""
    commits = _guidance_commits(git_dir, sha, globs)
    latest: dict[str, dict[str, str]] = {}
    for c in commits:  # newest first: the first commit naming a file is its latest
        for path in c.paths:
            latest.setdefault(path, c.dates())
    return {"guidance_latest": {p: latest[p] for p in sorted(files) if p in latest},
            "guidance_commits": [{**c.dates(), "subject": c.subject} for c in commits[:GUIDANCE_COMMITS_CAP]],
            "guidance_commits_truncated": len(commits) > GUIDANCE_COMMITS_CAP}


@lru_cache(maxsize=64)
def _guidance_commits(git_dir: Path, sha: str, globs: tuple[str, ...]) -> tuple[_Commit, ...]:
    """The commits reachable from ``sha`` that touch a path ``globs`` match, newest first by committer date."""
    found = []
    for record in _git_log(git_dir, sha).split("\x1e")[1:]:
        header, _, names = record.partition("\0")
        commit, authored, committed, subject = header.split("\x1f", 3)
        paths = tuple(p for p in names.lstrip("\n").split("\0") if p and any(match_glob(g, p) for g in globs))
        if paths:
            found.append(_Commit(commit, authored, committed, subject, paths))
    # stable: commits with one committer date keep git's order
    return tuple(sorted(found, key=lambda c: datetime.fromisoformat(c.committed), reverse=True))


def _git_log(git_dir: Path, sha: str) -> str:
    """Every commit reachable from ``sha`` with its dates, subject and the paths it touched."""
    keep = ("PATH", "LANG", "TMPDIR", "SYSTEMROOT")
    env = process.Env.only(keep, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": os.devnull,
                                  "GIT_TERMINAL_PROMPT": "0"})
    done = process.git("-C", str(git_dir), "log", "-z", "--no-renames", "--name-only", _FORMAT, sha, "--",
                       timeout=_GIT_TIMEOUT_S, env=env)
    if done.timed_out:
        raise PrepError(f"git log {sha[:12]}: timed out after {_GIT_TIMEOUT_S:.0f} seconds")
    if done.returncode:
        raise PrepError(f"git log {sha[:12]} exited {done.returncode}: {process.git_failure(done)}")
    return done.stdout
