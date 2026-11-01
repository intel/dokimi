# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build versioned per-subject scorecards."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dokimi import __version__

from .loader import awareness_arm_specs, work_arm_specs
from .models import SubjectDescriptor
from .novelty_track import build_novelty_block, novelty_warnings


def build_subject_scorecard(
    descriptor: SubjectDescriptor,
    *,
    awareness_runs: list[dict[str, Any]],
    out_dir: Path,
    work_run: dict[str, Any] | None = None,
    novelty_artifacts: list[dict[str, Any]] | None = None,
    novelty_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Build a subject_scorecard.v2 artifact from matrix rollup runs.

    ``novelty_artifacts`` are ``novelty.v1`` payloads produced separately by ``novelty analyze``
    and attached by path. They are not produced here: the measurement is
    ``atoms x probes x arms x (answer + grade)`` and would dominate a subject run's cost.
    """
    warnings: list[str] = []
    novelty = build_novelty_block(novelty_artifacts or [], sources=novelty_sources)
    warnings.extend(novelty_warnings(novelty))
    if descriptor.suite.tasks:
        if work_run is None:
            warnings.append(
                "task suites are declared but not executed; pass --work-harnesses "
                "or --skip-work to make the work status explicit"
            )
        else:
            warnings.extend(work_run.get("warnings", []))

    cells = []
    plugin_deltas = []
    for run in awareness_runs:
        rollup = run["rollup"]
        for cell in rollup.get("cells", []):
            cells.append({
                "product": run["product"],
                "questions": run["questions"],
                "matrix_cell": cell.get("matrix_cell"),
                "artifact": cell.get("artifact"),
                "model": cell.get("model"),
                "provider": cell.get("provider"),
                "harness": cell.get("harness"),
                "plugin_set": cell.get("plugin_set"),
                "summary": cell.get("summary", {}),
                "cost_summary": cell.get("cost_summary", {}),
            })
        for row in rollup.get("plugin_deltas", []):
            item = dict(row)
            item["product"] = run["product"]
            item["questions"] = run["questions"]
            plugin_deltas.append(item)
        warnings.extend(rollup.get("warnings", []))

    return {
        "schema_version": "subject_scorecard.v2",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "subject": {
            "id": descriptor.subject.id,
            "kind": descriptor.subject.kind,
            "ref": descriptor.subject.ref,
            "ref_digest": _digest_ref(descriptor.subject.ref, base=Path.cwd()),
            "distribution_digest": distribution_digest(descriptor.subject.ref, base=Path.cwd()),
            "members": [
                {
                    "kind": member.kind,
                    "ref": member.ref,
                    "ref_digest": _digest_ref(member.ref, base=Path.cwd()),
                    "distribution_digest": distribution_digest(member.ref, base=Path.cwd()),
                }
                for member in descriptor.subject.members
            ],
        },
        "descriptor": str(descriptor.path),
        "suite": {
            "products": list(descriptor.suite.products),
            "questions": list(descriptor.suite.questions),
            "question_set_hash": _digest_values(descriptor.suite.questions),
            "tasks": list(descriptor.suite.tasks),
        },
        "baseline": descriptor.baseline,
        "baseline_digest": _digest_arm_spec(descriptor.baseline, base=Path.cwd()),
        "awareness": {
            "arm_specs": awareness_arm_specs(descriptor),
            "runs": [
                {
                    "product": run["product"],
                    "questions": run["questions"],
                    "matrix_rollup": run["matrix_rollup"],
                }
                for run in awareness_runs
            ],
        },
        "work": {
            "status": work_run.get("status", "not_run") if work_run else "not_run",
            "arm_specs": work_arm_specs(descriptor),
            "tasks": list(descriptor.suite.tasks),
            "harnesses": work_run.get("harnesses", []) if work_run else [],
            "task_runs": work_run.get("task_runs") if work_run else None,
            "task_report": work_run.get("task_report") if work_run else None,
            "summary": work_run.get("summary", {}) if work_run else {},
        },
        "cells": cells,
        "novelty": novelty,
        "plugin_deltas": plugin_deltas,
        "run_manifest": {
            "dokimi_version": __version__,
            "out_dir": str(out_dir),
            "matrix_cells": len(descriptor.matrix_cells),
            "awareness_runs": len(awareness_runs),
        },
        "warnings": sorted(set(warnings)),
    }


def _digest_values(values: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for value in values:
        h.update(value.encode("utf-8"))
        h.update(b"\0")
        path = Path(value)
        if path.is_file():
            h.update(path.read_bytes())
    return "sha256:" + h.hexdigest()


def distribution_digest(ref: str | None, *, base: Path) -> str | None:
    """The digest a ``.well-known/agent-skills`` index would publish for *ref*, or None.

    That index (agentskills PR #254) hashes the bytes it serves: one ``SKILL.md`` for a
    ``skill-md`` entry, the archive for an ``archive`` entry. Only the first can be derived
    from a checkout: a ``SKILL.md`` file ref is hashed as is, and a directory whose only file is
    ``SKILL.md`` is hashed as that file. A directory with resources is served as an archive,
    whose bytes depend on how it was packed, so it gets None rather than a digest no index
    carries. So does a ref that is not a local path. ``ref_digest`` stays a content digest
    of the tree.
    """
    if not ref:
        return None
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]+:", ref) and not ref.startswith("local:"):
        return None
    path = Path(ref.removeprefix("local:"))
    if not path.is_absolute():
        path = base / path
    if path.is_dir():
        # Any other entry makes it an archive; one stray file is enough, so stop at the second.
        if [p.name for _, p in zip(range(2), path.iterdir(), strict=False)] != ["SKILL.md"]:
            return None
        path = path / "SKILL.md"
    if path.name != "SKILL.md" or not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest_ref(ref: str | None, *, base: Path) -> str | None:
    if not ref:
        return None
    path = Path(ref.removeprefix("local:"))
    if not path.is_absolute():
        path = base / path
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    elif path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(str(child.relative_to(path)).encode("utf-8"))
            h.update(b"\0")
            h.update(child.read_bytes())
            h.update(b"\0")
    else:
        h.update(ref.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def _digest_arm_spec(spec: str, *, base: Path) -> str:
    from dokimi.treatments.specs import content_refs

    h = hashlib.sha256()
    h.update(spec.encode("utf-8"))
    h.update(b"\0")
    for part, content_ref in content_refs(spec):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
        digest = _digest_ref(content_ref, base=base)
        h.update((digest or "").encode("utf-8"))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()
