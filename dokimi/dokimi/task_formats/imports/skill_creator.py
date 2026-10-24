# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""anthropics/skills skill-creator ``evals/evals.json`` as dokimi questions.

Schema: ``skills/skill-creator/references/schemas.md`` (``skill_name``, ``evals[]`` with ``id``,
``prompt``, ``expected_output``, ``files``, ``expectations``; ``SKILL.md`` calls the same list
``assertions``). Its grader (``agents/grader.md``) marks each expectation PASS/FAIL against the
run's transcript and output files and reports the pass rate. Here each expectation is one
judge check over the answer text, and the score is the share that pass; with no expectations,
``expected_output`` is the one criterion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dokimi.task_formats.imports.common import Imported, judge, question
from dokimi.task_formats.source import FileSource

FORMAT = "skill-creator-evals"


def _statement(item: Any) -> str:
    """An expectation as text; a dict assertion's ``text``/``name``/``description``."""
    if isinstance(item, Mapping):
        return str(item.get("text") or item.get("description") or item.get("name") or "").strip()
    return str(item).strip()


def import_file(path: Path) -> Imported:
    data = FileSource(path).data
    if not isinstance(data, Mapping) or not isinstance(data.get("evals"), list):
        raise ValueError(f"{path}: not a skill-creator evals.json (no `evals` list)")
    imp = Imported(FORMAT, path, native_scorer="grader.md expectations")
    imp.note("graded on the answer text, not a transcript or output files")
    skill = data.get("skill_name")
    for i, row in enumerate(data["evals"]):
        if not isinstance(row, Mapping) or not str(row.get("prompt") or "").strip():
            imp.note("eval without prompt skipped")
            continue
        statements = [s for s in map(_statement, [*row.get("expectations", []), *row.get("assertions", [])]) if s]
        expected = str(row.get("expected_output") or "").strip()
        if statements:
            checks = [judge(criterion=s, native="expectation") for s in statements]
        elif expected:
            imp.note("no expectations: expected_output used as the criterion")
            checks = [judge(criterion=expected, native="expected_output")]
        else:
            imp.note("eval with neither expectations nor expected_output skipped")
            continue
        files = [str(f) for f in row.get("files") or []]
        if files:
            imp.note("input files not attached (answer-only)")
        meta: dict[str, Any] = {"skill_name": skill, "expected_output": expected or None, "files": files}
        imp.questions.append(question(
            row.get("id", i), str(row["prompt"]), {"checks": checks, "native_scorer": "expectations"},
            source_format=FORMAT, path=path, references=statements or [expected],
            metadata={k: v for k, v in meta.items() if v}))
    return imp
