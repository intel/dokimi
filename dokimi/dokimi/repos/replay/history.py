# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What building a replay task asks of the source's history, read with git plumbing.

``diff-tree`` and not ``diff``: porcelain options such as ``diff.noprefix`` in the user's
configuration would change a patch ``git am`` must apply. Every call goes through
:func:`dokimi.process.git`. Diffs, parents and authors are kept: a commit's never change.

A hunk is ``(old start, old count, new start, new count)`` as ``-U0`` writes it; a count of 0
is an insertion after (old side) or a deletion before (new side) that line. A deleted file is
one hunk over all its lines, so the line arithmetic needs no case of its own for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path

from dokimi import process

from .threads import ReplayError

Hunk = tuple[int, int, int, int]

_TIMEOUT_S = 600.0
_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


class History:
    """Read-only git questions about one git directory."""

    def __init__(self, git_dir: Path):
        self.git_dir = git_dir
        self._changes: dict[tuple[str, str], dict[str, str]] = {}  # old path: new path
        self._hunks: dict[tuple[str, str, str], list[Hunk]] = {}
        self._parents: dict[str, list[str]] = {}
        self._bots: dict[str, bool] = {}

    def _git(self, *args: str, check: bool = True, literal: bool = True) -> process.Completed:
        flags = ["--literal-pathspecs"] if literal else []
        done = process.git("--git-dir", str(self.git_dir), *flags, *args, timeout=_TIMEOUT_S)
        if done.timed_out:
            raise ReplayError(f"git {args[0]} timed out after {_TIMEOUT_S:.0f} seconds")
        if check and done.returncode:
            raise ReplayError(f"git {args[0]} failed: {process.git_failure(done)}")
        return done

    def has(self, sha: str) -> bool:
        return self._git("cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0

    def fetch(self, shas: Iterable[str]) -> None:
        """Fetch from ``origin`` the commits missing here; one that no longer exists there stays missing."""
        missing = [s for s in dict.fromkeys(shas) if not self.has(s)]
        if missing and self._git("fetch", "--quiet", "--no-tags", "origin", "--", *missing, check=False).returncode:
            for sha in missing:  # one unknown SHA fails the whole fetch: the rest one at a time
                self._git("fetch", "--quiet", "--no-tags", "origin", "--", sha, check=False)

    def parents(self, sha: str) -> list[str]:
        if sha not in self._parents:
            self._parents[sha] = self._git("rev-list", "--parents", "-n", "1", sha, "--").stdout.split()[1:]
        return self._parents[sha]

    def responds(self, sha: str) -> bool:
        """Whether commit ``sha`` can be an author's response to a review: it has one parent (it is no
        merge), and no bot wrote it (``[bot]`` in its author's name or email, as pre-commit.ci's fixes)."""
        if sha not in self._bots:
            raw = self._git("cat-file", "commit", sha).stdout_bytes
            author = next((ln for ln in raw.split(b"\n") if ln.startswith(b"author ")), b"")
            self._bots[sha] = b"[bot]" in author
        return len(self.parents(sha)) == 1 and not self._bots[sha]

    def merge_base(self, a: str, b: str) -> str | None:
        done = self._git("merge-base", a, b, check=False)
        return (done.stdout.strip() or None) if done.returncode == 0 else None

    def chain(self, start: str, head: str) -> list[str] | None:
        """The first-parent commits after ``start`` up to ``head``, oldest first; ``None`` when that
        path does not start at ``start`` (``head`` does not descend from it, or only through a merge's
        second parent)."""
        if start == head:
            return []
        if self._git("merge-base", "--is-ancestor", start, head, check=False).returncode:
            return None
        commits = self._git("rev-list", "--first-parent", "--reverse", head, f"^{start}", "--").stdout.split()
        return commits if commits and self.parents(commits[0])[:1] == [start] else None

    def change(self, a: str, b: str, path: str) -> str | None:
        """What ``path`` is called at ``b`` when it changed from ``a`` to ``b`` (renamed, edited or
        deleted); ``None`` when it did not."""
        if (a, b) not in self._changes:
            raw = self._git("diff-tree", "-r", "-z", "--name-status", "-M", a, b).stdout_bytes
            self._changes[(a, b)] = {old: new for _, old, new in _entries(raw) if old}
        return self._changes[(a, b)].get(path)

    def path_at(self, rev: str, path: str, later: str) -> str | None:
        """What ``path`` was called at ``rev``; ``None`` when ``rev`` has no such file.

        GitHub gives a thread its file's latest name, so a file renamed after the thread is followed
        back from ``later`` one commit at a time (``git log --follow``): one diff over the range
        shows a file that was edited and renamed as deleted and added."""
        name = path
        if not self._exists(rev, name):
            done = self._git("log", "--follow", "-M", "--name-status", "-z", "--format=", "--no-show-signature",
                             f"{rev}..{later}", "--", path, check=False)
            for status, old, new in _entries(done.stdout_bytes if done.returncode == 0 else b""):
                if status == "R" and new == name:
                    name = old
        return name if self._exists(rev, name) else None

    def steps(self, a: str, b: str) -> list[tuple[str, str]]:
        """The diffs that lead from ``a`` to ``b`` one commit at a time: along the first-parent path
        from ``a`` to ``b``, or back along the one from ``b`` to ``a``; ``(a, b)`` when neither exists."""
        forward = self.chain(a, b)
        if forward is not None:
            return list(pairwise([a, *forward]))
        back = self.chain(b, a)
        if back is not None:
            return list(pairwise(reversed([b, *back])))
        return [(a, b)]

    def _exists(self, rev: str, path: str) -> bool:
        return self._git("cat-file", "-e", f"{rev}:{path}", check=False).returncode == 0

    def hunks(self, a: str, b: str, path: str, new_path: str) -> list[Hunk]:
        """The ``-U0`` hunks of ``path`` (``new_path`` at ``b``); none for a binary file."""
        if (a, b, path) not in self._hunks:
            raw = self._git("diff-tree", "-r", "-p", "-U0", "-M", a, b, "--", path, new_path).stdout_bytes
            self._hunks[(a, b, path)] = [_hunk(m) for m in _HUNK.finditer(raw)]
        return self._hunks[(a, b, path)]

    def lines(self, rev: str, path: str) -> int | None:
        """How many lines ``path`` has at ``rev``; ``None`` when it is not a file there."""
        done = self._git("cat-file", "blob", f"{rev}:{path}", check=False)
        if done.returncode:
            return None
        data = done.stdout_bytes
        return data.count(b"\n") + (bool(data) and not data.endswith(b"\n"))

    def changed(self, a: str, b: str) -> list[str]:
        """Every path that differs between ``a`` and ``b``, a rename as its two paths."""
        raw = self._git("diff-tree", "-r", "-z", "--name-only", "--no-renames", a, b).stdout_bytes
        return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]

    def patch(self, a: str, b: str, leave_out: Iterable[str]) -> bytes:
        """The diff from ``a`` to ``b`` that ``git apply`` takes, binary files included, without ``leave_out``."""
        spec = [f":(exclude,literal){p}" for p in leave_out]  # only excludes: the rest of the tree
        args = ["diff-tree", "-r", "-p", "--binary", "--full-index", "--no-renames", a, b]
        return self._git(*args, *(["--", *spec] if spec else []), literal=False).stdout_bytes


def map_line(line: int, hunks: list[Hunk]) -> int | None:
    """Where old line ``line`` is after ``hunks``; ``None`` when they changed or removed it."""
    shift = 0
    for old, n_old, _, n_new in hunks:
        if n_old == 0:  # an insertion after old line ``old``
            if old >= line:
                break
        elif line < old:
            break
        elif line < old + n_old:
            return None
        shift += n_new - n_old
    return line + shift


def near(line: int, hunks: list[Hunk], window: int) -> bool:
    """Whether a hunk changes old lines within ``window`` of ``line`` (an insertion sits between two lines)."""
    for old, n_old, _, _ in hunks:
        lo, hi = (old, old + 1) if n_old == 0 else (old, old + n_old - 1)
        if lo - window <= line <= hi + window:
            return True
    return False


def addressed(h: History, commit: str, path: str, line: int, head: str, window: int) -> tuple[bool, str | None]:
    """``(addressed, why a maintainer should check it)`` for a thread on ``line`` of ``path`` at ``commit``.

    Walks the first-parent commits from ``commit`` to the PR's ``head`` and carries the line
    through each one. A commit that :meth:`History.responds` and changes the file within
    ``window`` lines of it, deleting the file among them, addresses the thread. Any other commit
    only moves the line (the base branch's commits reach the PR through merges, never on its
    first-parent path), and one that rewrites the line ends the walk. With no such path from
    ``commit`` to ``head`` the diff between the two is read instead.
    """
    chain = h.chain(commit, head)
    if chain is None:
        new_path = h.change(commit, head, path)
        done = new_path is not None and near(line, h.hunks(commit, head, path, new_path), window)
        return done, ("no first-parent path leads from its revision to the PR head (history rewritten, or "
                      "the revision reached through a merge), so the diff to the head is read")
    prev, cur = commit, line
    for c in chain:
        new_path = h.change(prev, c, path)
        if new_path is not None:
            hunks = h.hunks(prev, c, path, new_path)
            if h.responds(c) and near(cur, hunks, window):
                return True, None
            moved = map_line(cur, hunks)
            if moved is None:
                return False, f"{c[:12]}, a merge or a bot's commit, rewrote its line, so no later commit is read"
            cur, path = moved, new_path
        prev = c
    return False, None


def carry(h: History, a: str, b: str, path: str, line: int) -> tuple[str, int] | None:
    """``(path, line)`` at ``b`` of ``line`` of ``path`` at ``a``, carried through :meth:`History.steps`
    so that a rename on the way is followed; ``None`` when the line changed on the way."""
    for x, y in h.steps(a, b):
        new_path = h.change(x, y, path)
        if new_path is None:
            continue
        moved = map_line(line, h.hunks(x, y, path, new_path))
        if moved is None:
            return None
        path, line = new_path, moved
    return path, line


def _hunk(m: re.Match[bytes]) -> Hunk:
    return (int(m[1]), 1 if m[2] is None else int(m[2]), int(m[3]), 1 if m[4] is None else int(m[4]))


def _entries(raw: bytes) -> list[tuple[str, str, str]]:
    """``(status letter, old path, new path)`` per entry of a ``-z --name-status -M`` listing, one
    diff or a log's diffs one after another; an added file's old path is ``""``. ``-M`` without
    ``-C`` reports no copies."""
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0")]
    out = []
    i = 0
    while i + 1 < len(parts) and parts[i]:
        status = parts[i][0]
        if status == "R":  # R100: the score, then both paths
            if i + 2 < len(parts):
                out.append((status, parts[i + 1], parts[i + 2]))
            i += 3
        else:
            out.append((status, "" if status == "A" else parts[i + 1], parts[i + 1]))
            i += 2
    return out
