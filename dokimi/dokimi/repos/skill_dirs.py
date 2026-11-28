# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A skill's target directory in a workspace, read before a skill is installed over it."""

from __future__ import annotations

from pathlib import Path

from dokimi.digest_entries import listed_entries

from .errors import PrepError


def only_empty_dirs(path: Path) -> bool:
    """``path`` is a real directory with nothing but real directories under it. A directory under it that cannot be
    listed is a ``PrepError``: what it holds is unknown, and it must not read as empty and be removed."""
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        return all(p.is_dir() and not p.is_symlink() for p in listed_entries(path))
    except OSError as exc:
        raise PrepError(f"cannot list {path}: {exc}") from exc
