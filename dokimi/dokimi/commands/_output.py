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


def write_text_or_exit(path: Path, text: str, code: int = 1) -> None:
    """Write ``text`` to ``path`` (creating parents), or exit ``code`` naming ``path`` when it cannot be written.

    A command helper: library code that cannot write raises, and the command decides the exit.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(code)
