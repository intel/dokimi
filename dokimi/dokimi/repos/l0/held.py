# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The submodule paths a tree holds as directories, as a checkout does."""

from __future__ import annotations

from ..source import RepoTree, SourceArgumentError


def held_dirs(tree: RepoTree, paths: list[str]) -> set[str]:
    """The paths the tree holds as directories: a stale path, or one that leaves the tree, is not one."""
    held: set[str] = set()
    for m in paths:
        try:
            if tree.is_dir(m):
                held.add(m)
        except (SourceArgumentError, OSError, RuntimeError):  # RuntimeError: a symlink loop before 3.13
            pass
    return held
