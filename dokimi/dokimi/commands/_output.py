# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared guard for CLI output writes."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path


@contextlib.contextmanager
def exit_on_unwritable(path: Path):
    """Exit 1 naming ``path`` when an output write inside the block fails."""
    try:
        yield
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(1)
