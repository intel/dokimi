# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Path checks that tolerate the OS refusing a name, for the catalog validator.

A name over the filesystem limit (255 bytes in one part) raises ``OSError`` from ``stat``, and a
skill directory name is not always one the OS will resolve. These return an answer instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _resolved(path: Path) -> Optional[Path]:
    """``path`` resolved, or None when the OS refuses the name (a NUL byte, a symlink loop)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _exists(path: Path) -> bool:
    """``path.exists()``, where a name the OS refuses (over 255 bytes in one part, say) is absent."""
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _inside(path: Path, base: Path) -> bool:
    """Whether ``path`` lies under ``base`` by its written name, with ``..`` taken away. Reads no file."""
    return Path(os.path.abspath(path)).is_relative_to(os.path.abspath(base))


def _in_checkout(path: Path, source_root: Path) -> Optional[Path]:
    """``path`` resolved, or None unless it lies in the checkout both as written and where it resolves."""
    target = _resolved(path) if _inside(path, source_root) else None
    return target if target is not None and target.is_relative_to(source_root.resolve()) else None

