# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The up-front check of a cache path from a flag or variable, shared by the novelty and repo commands (#701, #765, #826)."""

from __future__ import annotations

import os
from pathlib import Path

RETENTION_USE = "the retention verdicts can be stored in"
CLONE_USE = "the repository clones can be cached in"
CACHE_USE = "the build caches can be kept in"


def usable_cache_dir(raw: str | None, use: str = RETENTION_USE, *, label: str = "--cache-dir") -> Path | None:
    """``raw`` (the value of ``--cache-dir`` or another setting) as a path a cache can store into, or ``None`` when unset.

    A directory that does not exist yet is fine (created on the first store) when an existing,
    writable directory is above it; one that exists must be a readable directory, writable or
    not (a fully cached run replays from it). Anything else raises ``InputError`` before a paid
    call or a clone (#701, #765). ``use`` closes the message: "pass a directory <use>"; ``label`` names the
    setting at its start (``--cache-dir`` unless the caller says otherwise: #826).
    """
    from dokimi.files import InputError

    if not raw:
        return None
    path = Path(raw)
    probe = path
    try:
        while not probe.exists() and not probe.is_symlink() and probe != probe.parent:
            probe = probe.parent
        is_dir = probe.is_dir()
    except OSError as exc:
        # An ancestor without search permission: exists()/is_dir() raise PermissionError (Python < 3.14).
        raise InputError(f"{label} {path}: cannot inspect {probe} ({exc.strerror or type(exc).__name__}); "
                         f"pass a directory {use}") from exc
    if not is_dir:
        where = "is not a directory" if probe == path else f"has {probe} above it, which is not a directory"
        raise InputError(f"{label} {path} {where}; pass a directory {use}")
    # An existing directory only has to be readable: a fully cached run replays from a read-only
    # directory and writes nothing. A missing one is created on the first store, so it needs to be writable.
    needed, word = (os.R_OK | os.X_OK, "readable") if probe == path else (os.W_OK | os.X_OK, "writable")
    if not os.access(probe, needed):
        raise InputError(f"{label} {path}: {probe} is not {word}; pass a directory {use}")
    return path


def resolved_cache_dir(raw: str | None, use: str, *, label: str) -> Path | None:
    """``usable_cache_dir`` of ``raw`` resolved the way the cache is made, so ``missing/..`` cannot hide a file (#827).

    A path that cannot be resolved (an unreadable ancestor, a symbolic link loop) raises ``InputError`` naming ``label``.
    The resolved path is returned: the cache is made there.
    """
    from dokimi.files import InputError

    if not raw:
        return None
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, RuntimeError) as exc:  # RuntimeError: a symlink loop, Python < 3.13
        why = exc.strerror if isinstance(exc, OSError) and exc.strerror else "a symbolic link loop"
        raise InputError(f"{label} {raw}: cannot resolve it ({why}); pass a directory {use}") from exc
    return usable_cache_dir(resolved, use, label=label)
