# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Built-in graders: ``task.json`` ``grader: "dokimi:<name>"`` (contract §4.5).

A task naming one is graded by framework code instead of the repository's
``grade`` and ``oracle`` entry points, so the task is data only. The modules
next to this file run inside the grading container: this directory is
mounted read-only at :data:`~dokimi.repos.container.GRADERS_DIR` and
:mod:`.main` runs on the image's ``python3``, with the standard library only.
This file is the host side and is never imported there.
"""

from __future__ import annotations

from pathlib import Path

from .review import GRADERS

PREFIX = "dokimi:"
NAMES = tuple(GRADERS)
# the data file each built-in grader reads next to task.json: the task key that renames it, and its default
DATA_FILES = {"review_seeds": ("manifest", "manifest.json"), "review_replay": ("gold", "gold.json")}
HOST_DIR = Path(__file__).resolve().parent
ENTRY = "main.py"


def builtin_name(grader: object) -> str | None:
    """``review_seeds`` for ``"dokimi:review_seeds"``; ``None`` for a repository's grader name.

    The whole ``dokimi:`` namespace is the framework's: an unknown name in it is returned as well,
    for the caller to refuse rather than hand to the repository's ``grade``."""
    if isinstance(grader, str) and grader.startswith(PREFIX):
        return grader[len(PREFIX):]
    return None
