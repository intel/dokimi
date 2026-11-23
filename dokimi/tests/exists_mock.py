# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`Path.exists` stand-in for tests whose doc source is only a name (#1086 refuses a missing local path)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def exists_for_prefix(real_exists: Callable[[Path], bool], prefix: str) -> Callable[[Path], bool]:
    """True for paths whose POSIX spelling starts with `prefix`; `\\docs` on Windows is `/docs` here."""
    return lambda self: self.as_posix().startswith(prefix) or real_exists(self)
