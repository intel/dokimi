# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which paths a dokimi_config subject claims: a glob claims the paths it matches and the directories above them, and
the directories that hold only suite content are left out of a skill's skill_sha, as the suite's own."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from dokimi.repo_links import _within


def _claims(pattern: str, segs: tuple[str, ...]) -> bool:
    """True when the glob matches ``segs`` or one of its parent directories (a directory glob claims what is under it)."""
    pat = tuple(pattern.split("/"))
    return any(_match(pat, segs[:k]) for k in range(1, len(segs) + 1))


def _match(pat: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    """The set of pattern positions reached after each segment, so k ``**`` cost no more than one (backtracking is
    exponential in k), with no recursion per segment."""
    states = _past_stars(pat, {0})
    for seg in segs:
        states = _past_stars(pat, {i if pat[i] == "**" else i + 1 for i in states
                                   if i < len(pat) and (pat[i] == "**" or fnmatch.fnmatchcase(seg, pat[i]))})
    return len(pat) in states


def _past_stars(pat: tuple[str, ...], states: set[int]) -> set[int]:
    """``states`` plus each position a run of ``**`` from it reaches by matching no directory."""
    out = set(states)
    for i in states:
        while i < len(pat) and pat[i] == "**":
            i += 1
            out.add(i)
    return out


def _ancestor_of(pattern: str, segs: tuple[str, ...]) -> bool:
    """True when ``segs`` is a strict ancestor of a path the glob ``pattern`` matches. Only asked of a changed path that
    is gone from disk: a link on the way to a suite that was deleted still changes what the suite reads, and no link
    is left to find, so the change claims the suite. (A ``**`` leading the pattern can match under any path.)"""
    return _ancestor(tuple(pattern.split("/")), segs)


def _ancestor(pat: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    if not segs:
        return bool(pat)
    if not pat:
        return False
    if pat[0] == "**":
        return True  # a ** below here may match anything under the path
    return fnmatch.fnmatchcase(segs[0], pat[0]) and _ancestor(pat[1:], segs[1:])


def _suite_prefixes(suite_globs: tuple[str, ...], path: str) -> list[str]:
    """The literal directories each suite glob starts with, inside the skill at ``path``: where its matches sit, whether or
    not any is left, so a suite directory that is emptied counts as the suite's."""
    found: list[str] = []
    for pattern in suite_globs:
        literal: list[str] = []
        for seg in pattern.split("/"):
            if any(c in seg for c in "*?["):
                break
            literal.append(seg)
        place = "/".join(literal)
        if _within(place, path) and place != path:
            found.append(place[len(path) + 1:])
    return found


def _suite_only_dirs(skill: Path, claimed: list[str], prefixes: list[str]) -> list[str]:
    """The directories under ``skill`` that hold nothing but suite content: an ancestor of a claimed path or of a suite
    glob's literal prefix, whose every entry is claimed or is such a directory itself. tree_digest hashes a directory by
    its name, so the first matched task (which creates evals/) or the last one removed (which leaves it empty) would move
    skill_sha though no skill file changed. A directory with any other entry stays in skill_sha."""
    names = {"/".join(parts[:k]) for rel in [*claimed, *prefixes] for parts in [rel.split("/")]
             for k in range(1, len(parts))}
    names.update(prefixes)
    covered = list(claimed)
    found: list[str] = []
    for rel in sorted(names, key=lambda r: (-r.count("/"), r)):  # deepest first: a directory's entries are decided before it
        here = skill / rel
        if here.is_symlink() or not here.is_dir():
            continue
        with os.scandir(here) as it:  # an unreadable directory raises here, as the digest's strict walk would
            children = [f"{rel}/{entry.name}" for entry in it]
        if all(any(_within(child, c) for c in covered) for child in children):
            found.append(rel)
            covered.append(rel)
    return found
