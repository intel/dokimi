# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which guidance files of other agents, and which of the own overlay, a harness loaded: the overlay part of the
reach payload (``overlay_report``), kept apart from the probe itself so ``reach.py`` stays under its size cap."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from .overlays import overlay_of

SEEN = ("loaded", "truncated", "fetched")  # the classes that mean the harness had (some of) the file


def _pinned_names(links: dict[str, tuple[str, ...]],
                  native: set[str] | frozenset[str]) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Each probed file with the names a load of it can be pinned on, and the aliases that cannot be pinned."""
    names: dict[str, tuple[str, ...]] = {}
    ambiguous: set[str] = set()
    for target, aliases in links.items():
        group = (target, *aliases)
        overlay_names = [n for n in group if overlay_of(n) is not None]
        # a native file in the probe can import the target (CLAUDE.md with @docs/shared.md): the load is not the alias's;
        # two overlay names name one file, so either could have been read
        if aliases and (native or len(overlay_names) > 1):
            ambiguous.update((*aliases, *overlay_names))
            names[target] = ()
        else:
            names[target] = group
    return names, ambiguous


def _loads(records: list[dict[str, Any]], names: dict[str, tuple[str, ...]], links: dict[str, tuple[str, ...]],
           ambiguous: set[str], foreign: set[str], own_files: set[str]) -> tuple[dict[str, dict[str, str]], set[str],
                                                                               set[str]]:
    """The foreign overlays and own overlay files that loaded, by the names they can be pinned on, and the
    aliases whose file loaded without a way to tell which of them was read."""
    cross: dict[str, dict[str, str]] = {}
    own_loaded: set[str] = set()
    unpinned: set[str] = set()
    for r in records:
        if r["class"] not in SEEN:
            continue
        for n in names.get(r["file"], ()):
            if n in foreign:
                cross.setdefault(n, {})[r["scenario"]] = r["class"]
            if n in own_files:
                own_loaded.add(n)
        unpinned.update(n for n in (r["file"], *links.get(r["file"], ())) if n in ambiguous)
    return cross, own_loaded, unpinned


def native_in_probe(seeded: list[Any], guidance_names: Iterable[str]) -> set[str]:
    """The harness-native names (the profile's guidance names) that a probed file or link carries. A native file in
    the probe can import a link's target; a configured name with no file in the probe cannot."""
    probed = {PurePosixPath(p).name for s in seeded for p in (s.path, *s.aliases)}
    return probed & {PurePosixPath(n).name for n in guidance_names}


def overlay_report(records: list[dict[str, Any]], own: str | None,
                   links: dict[str, tuple[str, ...]], native: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    """The overlay part of the payload: which of the ``own`` overlay files loaded, and which foreign overlays did
    (``cross_loaded``, a finding: the harness read guidance written for another agent). ``links`` maps each probed
    file to the links that name it: a file reached through an overlay-named link is an overlay under that name.

    A load is pinned on a link only when no other name of its file could have been read: the harness reads a
    file of a ``native`` name by itself, and an own and a foreign overlay can name one file. Those links are
    ``ambiguous`` when the file loaded, and are neither own nor cross loads."""
    every = {n for target, aliases in links.items() for n in (target, *aliases)}
    own_files = {n for n in every if own and overlay_of(n) == own}
    foreign = {n for n in every if overlay_of(n) not in (None, own)}
    names, ambiguous = _pinned_names(links, native)
    cross, own_loaded, unpinned = _loads(records, names, links, ambiguous, foreign, own_files)
    report: dict[str, Any] = {"own": own, "own_files": sorted(own_files), "own_loaded": sorted(own_loaded),
                              "foreign_files": sorted(foreign), "cross_loaded": dict(sorted(cross.items()))}
    if unpinned:
        report["ambiguous"] = sorted(unpinned)
    return report
