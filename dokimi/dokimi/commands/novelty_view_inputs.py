# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty view`: read the saved novelty artifact and atoms file (bad input raises, exit 2 by the CLI)."""

from __future__ import annotations

from pathlib import Path


def load_view_inputs(novelty_path: str, atoms_path: str):
    """``(artifact, atoms, spans)`` from the two files; an unreadable or invalid one raises InputError."""
    from dokimi.artifacts import load_artifact
    from dokimi.novelty.atoms import load_atoms

    artifact = load_artifact("novelty", Path(novelty_path))
    atoms, spans = load_atoms(atoms_path)
    return artifact, atoms, spans
