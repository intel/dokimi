# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The digest of one entry a repo config subject walks: a file by its content and executable bit, a link by
its target text. ``repo_config._digest_files`` digests each entry with these."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Mapping

from dokimi.utils import _tracked_modes, file_digest, link_text


def target_digest(target: Path, root_real: Path, modes: Mapping[str, bool] | None, holds: Mapping[Path, bytes]) -> str:
    """One walked entry's digest, as tree_digest gives a root of that kind: a file by its content and executable bit, a
    link by its target text, and nothing there by the digest of nothing."""
    if target.is_symlink():
        return hashlib.sha256(b"L .\0" + os.fsencode(link_text(target, target)) + b"\0").hexdigest()
    if not target.is_file():
        return hashlib.sha256().hexdigest()
    return file_digest(holds[target], exec_bit(target, root_real, modes))


def exec_bit(target: Path, root_real: Path, modes: Mapping[str, bool] | None) -> bool:
    """A file's executable bit as its digest holds it: the index's (read by the caller; git is asked for this one file
    when it was not read), else the disk bit of a file the index does not hold."""
    tracked = file_modes(target, root_real, modes)
    if tracked is None:
        tracked = _tracked_modes(target.parent, target.name)
    if target.name in tracked:
        return tracked[target.name]
    return bool(os.lstat(target).st_mode & stat.S_IXUSR)


def file_modes(target: Path, root_real: Path, modes: Mapping[str, bool] | None) -> dict[str, bool] | None:
    """The index's executable bit for one file, keyed as tree_digest keys a file it digests by itself. None when the
    index was not read (tree_digest then asks git). A file the index does not hold (untracked, outside the repository)
    gives no bit and digests by its disk bit, as tree_digest does for an untracked file; asking git for each one is
    the per-file process this avoids."""
    if modes is None:
        return None
    try:
        key = target.relative_to(root_real).as_posix()
    except ValueError:
        return {}
    if key in modes:
        return {target.name: modes[key]}
    # the outer index does not hold a file of a nested checkout: its own repository records the bit, so git is asked
    return None if in_nested_repo(target, root_real) else {}


def in_nested_repo(target: Path, root_real: Path) -> bool:
    """True when a directory between ``target`` and the repository root holds a ``.git`` of its own."""
    for parent in target.parents:
        if parent == root_real or not parent.is_relative_to(root_real):
            return False
        if (parent / ".git").exists():
            return True
    return False
