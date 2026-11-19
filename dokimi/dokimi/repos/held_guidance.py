# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The guidance files a run's workspace held as the agent started, for ``guidance_read`` and ``guidance_searched``
(contract §4.1): :func:`~dokimi.repos.runner.run_process_metrics` keeps only the paths this tells apart as held."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from .manifest import RepoManifest, match_glob
from .overlay_paths import _supplies_a_file
from .workspace import SNAPSHOT_AUTHOR, _git_env, _workspace_files


class HeldGuidance:
    """The guidance files a workspace held as the agent started, as the files they resolve to. ``path in held`` is
    true for a path that resolves, now, to one of them: a link into the workspace (``alias/AGENTS.md`` through a
    directory link, ``sub/AGENTS.md`` to ``../AGENTS.md``) counts, a file the agent made does not."""

    def __init__(self, ws: Path, files: Iterable[str]) -> None:
        self.root = os.path.realpath(ws)
        self.files = frozenset(self.resolved(p) for p in files)

    def resolved(self, path: str) -> str:
        """Where the workspace path ``path`` leads, relative to the workspace (``..`` first when it leaves it)."""
        return Path(os.path.relpath(os.path.realpath(os.path.join(self.root, path)), self.root)).as_posix()

    def __contains__(self, path: object) -> bool:
        return self.resolved(str(path)) in self.files


def held_guidance(run_dir: Path, manifest: RepoManifest) -> HeldGuidance:
    """The guidance files (``guidance.globs``) ``run_dir/workspace`` holds, as the agent starts: a dangling, cyclic or
    directory link (:func:`~dokimi.repos.overlay_paths._supplies_a_file`) supplies no file and is not one."""
    ws = run_dir / "workspace"
    held = [p for p in _workspace_files(ws, _git_env(SNAPSHOT_AUTHOR))
            if any(match_glob(g, p) for g in manifest.guidance_globs) and _supplies_a_file(ws, p)]
    return HeldGuidance(ws, held)
