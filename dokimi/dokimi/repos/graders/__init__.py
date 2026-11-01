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

from . import typecheck
from .review import GRADERS as REVIEW_GRADERS

PREFIX = "dokimi:"
NAMES = (*REVIEW_GRADERS, *typecheck.GRADERS)
# the data file each built-in grader reads next to task.json: the task key that renames it, and its default
DATA_FILES = {"review_seeds": ("manifest", "manifest.json"), "review_replay": ("gold", "gold.json")}
# graders whose options are the task's own fields, checked by ``tasks check`` instead of a data file
TASK_PROBLEMS = {"typecheck": typecheck.task_problems}
HOST_DIR = Path(__file__).resolve().parent
ENTRY = "main.py"


def builtin_name(grader: object) -> str | None:
    """``review_seeds`` for ``"dokimi:review_seeds"``; ``None`` for a repository's grader name.

    The whole ``dokimi:`` namespace is the framework's: an unknown name in it is returned as well,
    for the caller to refuse rather than hand to the repository's ``grade``."""
    if isinstance(grader, str) and grader.startswith(PREFIX):
        return grader[len(PREFIX):]
    return None


def graders_digest() -> str:
    """sha256 over the built-in grader sources: what a batch's score depends on beyond its ``eval_dir``."""
    import hashlib

    h = hashlib.sha256()
    for path in sorted(HOST_DIR.glob("*.py")):
        h.update(f"{path.name}\0".encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
