# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared guard for CLI output writes."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from dokimi.artifacts import ArtifactValidationError
from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE


@contextlib.contextmanager
def exit_on_unwritable(path: Path):
    """Exit 2 naming ``path`` when an output write inside the block fails (exit-code table in docs/quickstart.md)."""
    try:
        yield
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def save_artifact_or_exit(kind: str, payload: dict, path: Path, code: int = EXIT_USAGE) -> None:
    """``save_artifact`` of ``kind``, or exit ``code`` naming ``path`` when it cannot be written.

    A payload its schema refuses is a writer bug, so it exits 1 (the payload is kept at ``<path>.rejected``).
    """
    from dokimi.artifacts import ArtifactValidationError, save_artifact

    try:
        save_artifact(kind, payload, path)
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(code)
    except ArtifactValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
@contextlib.contextmanager
def exit_on_rejected(kind: str):
    """Exit 1 when the ``kind`` schema refuses a save inside the block; save_artifact keeps the payload at ``<name>.rejected``."""
    try:
        yield
    except ArtifactValidationError as exc:
        print(f"Error: {kind} artifact rejected: {exc}", file=sys.stderr)
        sys.exit(1)


def write_text_or_exit(path: Path, text: str, code: int = EXIT_USAGE) -> None:
    """Write ``text`` to ``path`` (creating parents), or exit ``code`` naming ``path`` when it cannot be written.

    A command helper: library code that cannot write raises, and the command decides the exit.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(code)
