# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance files a run's workspace held as the agent started, for ``guidance_read`` and ``guidance_searched``
(contract §4.1): :func:`~dokimi.repos.runner.run_process_metrics` keeps only the paths this tells apart as held."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from .container import WORKSPACE
from .manifest import RepoManifest, match_glob
from .overlay_paths import _link_target, _supplies_a_file
from .workspace import SNAPSHOT_AUTHOR, _git_env, _workspace_files


def _anchored(path: str, near: str = "") -> tuple[str, list[str]]:
    """``path`` as (its anchor, its names): ``/`` on POSIX, ``C:\\`` on Windows (the drive of ``near`` when ``path`` names
    none), so the first name is never the drive and a join with the anchor stays absolute."""
    drive, rest = os.path.splitdrive(path)
    anchor = (drive or os.path.splitdrive(near)[0]) + os.sep
    return anchor, rest.replace(os.altsep or os.sep, os.sep).split(os.sep)


class HeldGuidance:
    """The guidance files a workspace held as the agent started, as the files they resolve to. ``path in held`` is
    true for a path that leads to one of them through the links the workspace had at the start: a link into the
    workspace (``alias/AGENTS.md`` through a directory link, ``sub/AGENTS.md`` to ``../AGENTS.md``) counts, also
    after the agent removed or retargeted the link; a file or a link the agent made does not. An absolute link target
    under ``/eval/run/workspace`` (the workspace as the container names it) leads into the workspace."""

    def __init__(self, ws: Path, files: Iterable[str]) -> None:
        self.root = os.path.realpath(ws)
        self.links, self.dirs = self._walk()
        self.files = frozenset(os.path.normcase(at) for p in files if (at := self.resolved(p)) is not None)

    def _walk(self) -> tuple[dict[str, str], frozenset[str]]:
        """Each link of the workspace as it is now (its path, absolute from the real root, and where it points), and
        its directories that are no link; the paths are keyed as ``os.path.normcase`` writes them (lower case on Windows)."""
        links: dict[str, str] = {}
        dirs_here = {os.path.normcase(self.root)}
        for here, dirs, names in os.walk(self.root):
            if here == self.root and ".git" in dirs:
                dirs.remove(".git")
            for name in (*dirs, *names):
                path = os.path.join(here, name)
                if os.path.islink(path):
                    links[os.path.normcase(path)] = str(_link_target(Path(path)))
                elif name in dirs:
                    dirs_here.add(os.path.normcase(path))
        return links, frozenset(dirs_here)

    def resolved(self, path: str) -> str | None:
        """Where the workspace path ``path`` leads through the links of the start, relative to the workspace
        (``..`` first when it leaves it); None for a path that follows more than 40 links, as a loop does, for a link
        whose target goes ``..`` out of a name that was no directory at the start (the system fails there), and for a
        target on another drive (Windows)."""
        here, names = _anchored(os.path.join(self.root, path))
        pending, hops = [(n, False) for n in names[::-1]], 0  # (name, whether a link's target gave it)
        root = os.path.normcase(self.root)
        while pending:
            part, from_link = pending.pop()
            if part in ("", "."):
                continue
            if part == "..":
                key = os.path.normcase(here)
                if from_link and (key == root or key.startswith(root + os.sep)) and key not in self.dirs:
                    return None
                here = os.path.dirname(here)
                continue
            here = os.path.join(here, part)
            if (key := os.path.normcase(here)) in self.links:
                if hops == 40:
                    return None
                hops, target = hops + 1, self.links[key]
                if target == WORKSPACE or target.startswith(WORKSPACE + "/"):  # the agent's name for this workspace
                    target = self.root + target[len(WORKSPACE):]
                if os.path.isabs(target) or target[:1] in (os.sep, os.altsep):  # ``\x`` is rooted on the drive of the link
                    here, names = _anchored(target, here)
                else:
                    here, names = os.path.dirname(here), _anchored(os.sep + target)[1]
                pending += [(n, True) for n in names[::-1]]
        try:
            return os.path.relpath(here, self.root).replace(os.sep, "/")
        except ValueError:  # Windows: a target on another drive has no path from the root
            return None

    def __contains__(self, path: object) -> bool:
        at = self.resolved(str(path))
        return at is not None and os.path.normcase(at) in self.files


def held_guidance(run_dir: Path, manifest: RepoManifest) -> HeldGuidance:
    """The guidance files (``guidance.globs``) ``run_dir/workspace`` holds, as the agent starts: a dangling, cyclic or
    directory link (:func:`~dokimi.repos.overlay_paths._supplies_a_file`) supplies no file and is not one."""
    ws = run_dir / "workspace"
    held = [p for p in _workspace_files(ws, _git_env(SNAPSHOT_AUTHOR))
            if any(match_glob(g, p) for g in manifest.guidance_globs) and _supplies_a_file(ws, p)]
    return HeldGuidance(ws, held)
