# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The export's refusal of a working tree with paths that could not be looked at."""

from __future__ import annotations

import shutil
from pathlib import Path

from .errors import PrepError
from .manifest import _is_within
from .source import RepoTree


def refuse_skipped(tree: RepoTree, only: str | None, exclude: str | None, tmp: Path) -> None:
    """Remove ``tmp`` and raise when a skipped path is inside ``only`` (or a directory above it), not in ``exclude``."""
    skipped = [w for p, w in zip(getattr(tree, "skipped_paths", ()), getattr(tree, "skipped", ()), strict=True)
               if (not only or _is_within(p, only) or _is_within(only, p)) and not (exclude and _is_within(p, exclude))]
    if skipped:
        shutil.rmtree(tmp, ignore_errors=True)
        raise PrepError(f"{tree.label} has paths that cannot be looked at: " + "; ".join(skipped))
