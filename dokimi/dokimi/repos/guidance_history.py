# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance history of a ``guidance:rev:<rev>`` arm, for its ``meta.json`` ``arm_rev`` (contract §3).

A replay task's answer is public from its ``public_at`` (§4.6); a guidance commit made after it may
encode that review. :func:`arm_rev_history` records what a reader needs to check that: the commit
that last touched each guidance file at the revision, and the newest commits that touch a guidance
path, each with its author and committer date. The flag reads the committer date: ``git commit
--amend`` and a rebase keep the author date (https://github.com/intel/dokimi/issues/290).

The history is one ``git log -z --raw`` over the revision, filtered with :func:`~.manifest.match_glob`.
``-z`` ends every field with a NUL, and each file the commit touched is a ``:``-led status record
followed by its one path, so a subject or a path, whatever it holds, cannot pass for the other.
Git cuts a subject at a NUL (``git commit`` refuses one), so a subject is one field. The history is
read once per git dir, revision, globs and links in a process, also when cells of the arm prepare at
once: every cell asks the same. A key of the repository's own config that changes what the log
prints (as checked with git 2.51) is overridden by a flag (:data:`_PINNED`).

The arm installs a guidance link (``AGENTS.md -> docs/current -> GUIDE.md``) as the bytes of the file
it resolves to, so a commit that touches any path of its chain, an intermediate link or that file,
counts for the link's path, whether or not a glob matches that path.

A merge counts when its own result changes a guidance path against every parent
(``--diff-merges=dense-combined``): a conflict resolution, an edit made in the merge, or a clean
merge that joins its parents' edits to different parts of one file. A merge that takes a file as
one parent has it does not: the commits it merged count.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterator
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
_FORMAT = "--format=%H%x00%aI%x00%cI%x00%s"  # with -z, a NUL ends the subject too
_COMMIT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256
#: Each overrides a key of the repository's config that changes the output (git 2.51)
_PINNED = (
    "--no-show-signature",  # log.showSignature: verification text outside the fields
    "--root",  # log.showRoot=false: the root commit with no files
    "--encoding=UTF-8",  # i18n.logOutputEncoding: a subject re-encoded, in UTF-16 with NULs
    "--ignore-submodules=none",  # diff.ignoreSubmodules=all: no commit that adds or moves a gitlink
)

_Links = tuple[tuple[str, tuple[str, ...]], ...]  # (link path, the paths it resolves through), sorted


@dataclass(frozen=True)
class _Commit:
    sha: str
    authored: str
    committed: str
    subject: str
    paths: tuple[str, ...]  # the guidance paths it touched

    def dates(self) -> dict[str, str]:
        return {"sha": self.sha, "authored": self.authored, "committed": self.committed}


def arm_rev_history(git_dir: Path, globs: tuple[str, ...], sha: str, files: list[str],
                    links: dict[str, list[str]]) -> dict[str, Any]:
    """``guidance_latest`` for each of ``files`` (the guidance at ``sha``; ``links`` maps those that
    are links to every path they resolve through, the file they resolve to last), ``guidance_commits``
    (newest first by committer date, at most :data:`GUIDANCE_COMMITS_CAP`) and
    ``guidance_commits_truncated``."""
    commits = _guidance_commits(git_dir, sha, globs, tuple(sorted((k, tuple(v)) for k, v in links.items())))
    latest: dict[str, dict[str, str]] = {}
    for c in commits:  # newest first: the first commit naming a file is its latest
        for path in c.paths:
            latest.setdefault(path, c.dates())
    return {"guidance_latest": {p: latest[p] for p in sorted(files) if p in latest},
            "guidance_commits": [{**c.dates(), "subject": c.subject} for c in commits[:GUIDANCE_COMMITS_CAP]],
            "guidance_commits_truncated": len(commits) > GUIDANCE_COMMITS_CAP}


_key_locks: dict[tuple[Any, ...], threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _guidance_commits(git_dir: Path, sha: str, globs: tuple[str, ...], links: _Links) -> tuple[_Commit, ...]:
    """:func:`_read_guidance_commits`, one thread per key at a time: a cell that asks while another
    cell reads the same history waits for that read and takes its answer from the cache."""
    key = (git_dir, sha, globs, links)
    with _key_locks_guard:
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:
        return _read_guidance_commits(git_dir, sha, globs, links)


@lru_cache(maxsize=64)
def _read_guidance_commits(git_dir: Path, sha: str, globs: tuple[str, ...], links: _Links) -> tuple[_Commit, ...]:
    """The commits reachable from ``sha`` that touch a path ``globs`` match or a path a link of ``links``
    resolves through, newest first by committer date."""
    linked: dict[str, list[str]] = {}
    for link, chain in links:
        for path in chain:
            linked.setdefault(path, []).append(link)
    found = []
    for (commit, authored, committed, subject), touched in _parse_log(_git_log(git_dir, sha)):
        paths = tuple(dict.fromkeys(g for p in touched for g in _counts_for(p, globs, linked)))
        if paths:
            found.append(_Commit(commit, authored, committed, subject, paths))
    # stable: commits with one committer date keep git's order
    return tuple(sorted(found, key=lambda c: datetime.fromisoformat(c.committed), reverse=True))


def _counts_for(path: str, globs: tuple[str, ...], linked: dict[str, list[str]]) -> list[str]:
    """The guidance paths a change to ``path`` counts for: itself when a glob matches it, and each link through it."""
    return ([path] if any(match_glob(g, path) for g in globs) else []) + linked.get(path, [])


def _parse_log(out: str) -> list[tuple[tuple[str, str, str, str], list[str]]]:
    """Each commit of :func:`_git_log`'s output as ``((sha, authored, committed, subject), paths)``.
    A field that starts with ``:`` (after the newline that ends a header) is a status record, and the
    field after it is its path; a path is never read as a header. Any other non-empty field starts a
    header of four fields; an empty one ends a merge's header, and the output."""
    fields = iter(out.split("\0"))
    commits: list[tuple[tuple[str, str, str, str], list[str]]] = []
    for field in fields:
        if field.lstrip("\n").startswith(":") and commits:
            commits[-1][1].append(next(fields, ""))
        elif field:
            commits.append((_header(field, fields), []))
    return commits


def _header(sha: str, fields: Iterator[str]) -> tuple[str, str, str, str]:
    authored, committed, subject = next(fields, ""), next(fields, ""), next(fields, "")
    if not _COMMIT_ID.fullmatch(sha):
        raise PrepError(f"git log: a commit starts with {sha[:60]!r}, not a commit id")
    return sha, authored, committed, subject


def _git_log(git_dir: Path, sha: str) -> str:
    """Every commit reachable from ``sha`` with its dates, subject and the paths it touched; a merge
    with the paths its result changes against every parent."""
    keep = ("PATH", "LANG", "TMPDIR", "SYSTEMROOT")
    env = process.Env.only(keep, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": os.devnull,
                                  "GIT_TERMINAL_PROMPT": "0"})
    done = process.git("-C", str(git_dir), "log", "-z", "--raw", "--no-abbrev", "--no-renames",
                       "--diff-merges=dense-combined", *_PINNED, _FORMAT, sha, "--", timeout=_GIT_TIMEOUT_S, env=env)
    if done.timed_out:
        raise PrepError(f"git log {sha[:12]}: timed out after {_GIT_TIMEOUT_S:.0f} seconds")
    if done.returncode:
        raise PrepError(f"git log {sha[:12]} exited {done.returncode}: {process.git_failure(done)}")
    return done.stdout
