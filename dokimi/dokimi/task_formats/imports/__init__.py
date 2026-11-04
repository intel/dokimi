# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Import adapters: answer-only eval formats -> dokimi questions with ``grading`` checks.

The canonical model is the questions.v2 record: ``question`` (the prompt, sent verbatim),
``expected_topics`` (reference answers / points, for the judge and ``eval score checklist``) and
``grading`` (format-neutral checks, :mod:`dokimi.eval.reference_checks`). Each format here is
only a translation into it; answering and scoring are the answer track's own
(``answers generate``, ``arms run``, ``eval score reference``). :func:`import_path` picks the adapter
from the format :func:`~dokimi.task_formats.detect` reports, or the one named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from dokimi.task_formats.imports import inspect_ai, openai_evals, promptfoo, skill_creator
from dokimi.task_formats.imports.common import PROMPT_VERBATIM, Imported

#: Format name (as ``eval inspect`` reports it) -> adapter taking ``(path, options)``.
ADAPTERS: dict[str, Callable[..., Imported]] = {
    inspect_ai.FORMAT_SAMPLES: lambda p, scorer=None, **_: inspect_ai.import_samples(p, scorer=scorer),
    inspect_ai.FORMAT_TASK: lambda p, task=None, **_: inspect_ai.import_task(p, task=task),
    openai_evals.FORMAT: lambda p, grader=None, **_: openai_evals.import_file(p, grader=grader),
    skill_creator.FORMAT: lambda p, **_: skill_creator.import_file(p),
    promptfoo.FORMAT: lambda p, **_: promptfoo.import_file(p),
}


def import_path(path: Path, *, fmt: Optional[str] = None, **options: Optional[str]) -> Imported:
    """``path`` imported by the adapter for ``fmt`` (detected when ``None``).

    ``options``: ``scorer`` (Inspect samples), ``task`` (Inspect ``@task`` name), ``grader``
    (OpenAI evals registry YAML or template).
    """
    if fmt is None:
        from dokimi.task_formats.detect import detect

        report = detect(Path(path))
        if report is None:
            raise ValueError(f"{path}: no known eval format detected (see `eval inspect {path}`)")
        if report.alternatives:
            # A file alone often fits two formats (Inspect samples vs OpenAI evals rows); only
            # the config that names it can tell, so a guess would import the wrong graders.
            candidates = ", ".join([report.format, *(str(a["format"]) for a in report.alternatives)])
            raise ValueError(f"{path}: ambiguous format ({candidates}); pick one with --format")
        fmt = report.format
    if fmt not in ADAPTERS:
        raise ValueError(f"{path}: format {fmt!r} has no import adapter; answer-only formats with one: "
                         f"{', '.join(sorted(ADAPTERS))}")
    imported = ADAPTERS[fmt](Path(path), **options)
    if not imported.questions:
        raise ValueError(f"{path}: {fmt} import produced no questions ({dict(imported.lossy)})")
    return imported


__all__ = ["ADAPTERS", "PROMPT_VERBATIM", "Imported", "import_path"]
