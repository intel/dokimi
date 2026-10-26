# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown discovery and loading helpers."""

from pathlib import Path
from typing import Iterable


# Directories whose markdown is someone else's (package READMEs, VCS metadata), not the docs under test.
_SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages"})


def discover_markdown(root: Path) -> list[Path]:
    """Return sorted markdown files under ``root``, skipping vendored and VCS directories."""
    return sorted(
        p for p in root.rglob("*.md")
        if p.is_file() and _SKIPPED_DIRS.isdisjoint(p.relative_to(root).parts[:-1])
    )


def load_text(path: Path) -> str:
    """Load a UTF-8 text file, replacing invalid bytes."""
    return path.read_text(encoding="utf-8", errors="replace")


def load_docs(paths: Iterable[Path]) -> dict[str, str]:
    """Load all documents into a deterministic path->text mapping."""
    return {str(p): load_text(p) for p in sorted(paths)}
