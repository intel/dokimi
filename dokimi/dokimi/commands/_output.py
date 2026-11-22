# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shared guard for CLI output writes."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from dokimi.artifacts import ArtifactValidationError
from dokimi.atomic_write import write_bytes_in_place_or_atomic
from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE
from dokimi.process_io import text_file_bytes


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
    from dokimi.artifacts import save_artifact

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
        write_report_text(path, text)
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(code)


def write_report_text(path: Path, text: str) -> None:
    """Publish a Markdown or HTML report at ``path`` (creating parents); raises ``OSError`` when it cannot.

    A plain file is replaced whole and keeps its group; a symlink, a hard-linked, special or read-only file, or one in a group the user is not in, is written in place as ``write_text`` did, which is not atomic.

    A lone surrogate in ``text`` (a name from JSON, YAML or argv) is written as ``?`` and a pair is joined,
    where ``Path.write_text`` raises ``UnicodeEncodeError``; other text is written byte for byte as it does.
    """
    write_bytes_in_place_or_atomic(path, text_file_bytes(text))
