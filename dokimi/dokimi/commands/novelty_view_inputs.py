# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`novelty view`: read the saved novelty artifact and atoms file, or exit 2 naming the bad one."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn
from dokimi.exit_codes import EXIT_USAGE


def _fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(EXIT_USAGE)


def load_view_inputs(novelty_path: str, atoms_path: str):
    """``(artifact, atoms, spans)`` from the two files; an unreadable or invalid one exits 2."""
    from dokimi.artifacts import load_artifact
    from dokimi.novelty.atoms import load_atoms

    try:
        artifact = load_artifact("novelty", Path(novelty_path))
    except OSError as exc:
        _fail(f"cannot read {novelty_path}: {exc}")
    except (ValueError, RecursionError) as exc:  # not UTF-8, not JSON, or not a novelty artifact
        _fail(f"{novelty_path} is not a valid novelty artifact: {exc}")
    try:
        atoms, spans = load_atoms(atoms_path)
    except OSError as exc:
        _fail(f"cannot read {atoms_path}: {exc}")
    except (ValueError, RecursionError) as exc:  # not UTF-8, not JSON, or not an atoms record
        _fail(f"{atoms_path} is not a valid atoms file: {exc}")
    return artifact, atoms, spans
