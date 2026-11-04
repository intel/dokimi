# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty view`: read the saved novelty artifact and atoms file (bad input raises, exit 2 by the CLI)."""

from __future__ import annotations

from pathlib import Path


def load_view_inputs(novelty_path: str, atoms_path: str):
    """``(artifact, atoms, spans)`` from the two files; an unreadable or invalid one raises InputError.

    An older capped atoms file that holds only its sample is said on stderr (#371).
    """
    from dokimi.artifacts import load_artifact
    from dokimi.commands.novelty_atoms_file import warn_capped_sample
    from dokimi.novelty.atoms import load_atoms_record

    artifact = load_artifact("novelty", Path(novelty_path))
    atoms, spans, saved = load_atoms_record(atoms_path)
    warn_capped_sample(saved, atoms_path)
    return artifact, atoms, spans
