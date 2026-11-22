# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build versioned per-subject scorecards."""

from __future__ import annotations

import errno
import hashlib
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dokimi import __version__
from dokimi.utils import key_bytes

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

    ``novelty_artifacts`` are ``novelty.v1`` payloads produced separately by ``skill novelty analyze``
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
                "baseline_arm": cell.get("baseline_arm"),
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


def _field(text: str) -> bytes:
    """A fixed-width field for *text*, so a NUL or any byte in it cannot run into the next field."""
    return hashlib.sha256(key_bytes(text)).digest()


_ABSENT_ERRNOS = (errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP, errno.ENAMETOOLONG)
_ABSENT_WINERRORS = (123, 206, 1921)  # invalid name, name too long (ENAMETOOLONG), cannot resolve


def _stat_mode(path: Path) -> int | None:
    """The st_mode of *path*, None when it names nothing (missing, a name past the file-name limit, a loop); permission and I/O errors propagate.

    ``Path.is_file()`` and ``is_dir()`` return False for every OSError on Python 3.14, so they would hide those errors.
    """
    try:
        return path.stat().st_mode
    except OSError as exc:
        if exc.errno in _ABSENT_ERRNOS or getattr(exc, "winerror", None) in _ABSENT_WINERRORS:
            return None
        raise
    except ValueError:  # an embedded NUL
        return None


def _is_file(path: Path) -> bool:
    mode = _stat_mode(path)
    return mode is not None and stat.S_ISREG(mode)


def _is_dir(path: Path) -> bool:
    mode = _stat_mode(path)
    return mode is not None and stat.S_ISDIR(mode)


def _digest_values(values: tuple[str, ...]) -> str:
    """Per value: its text's digest, then a tag for file or no file, then the file's digest when it names one."""
    h = hashlib.sha256()
    for value in values:
        h.update(_field(value))
        path = Path(value)
        if _is_file(path):
            h.update(b"f" + hashlib.sha256(path.read_bytes()).digest())
        else:
            h.update(b"t")
    return "sha256:" + h.hexdigest()


def distribution_digest(ref: str | None, *, base: Path) -> str | None:
    """The digest a ``.well-known/agent-skills`` index would publish for *ref*, or None.

    That index (agentskills PR #254) hashes the bytes it serves: one ``SKILL.md`` for a
    ``skill-md`` entry, the archive for an ``archive`` entry. Only the first can be derived
    from a checkout. A ``SKILL.md`` is hashed as is when its directory holds nothing else,
    whether the ref names the file or the directory. A skill whose directory holds resources
    is served as an archive, whose bytes depend on how it was packed, so it gets None rather
    than a digest no index carries, under either spelling of its ref. So does a ref that is
    not a local path. ``ref_digest`` stays a content digest of the tree.
    """
    if not ref:
        return None
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]+:", ref) and not ref.startswith("local:"):
        return None
    path = Path(ref.removeprefix("local:"))
    if not path.is_absolute():
        path = base / path
    if _is_dir(path):
        skill_dir = path
    elif path.name == "SKILL.md" and _is_file(path):
        skill_dir = path.parent
    else:
        return None
    skill = skill_dir / "SKILL.md"
    # Any other entry makes it an archive; one stray file is enough, so stop at the second.
    if [p.name for _, p in zip(range(2), skill_dir.iterdir(), strict=False)] != ["SKILL.md"] or not _is_file(skill):
        return None
    return "sha256:" + hashlib.sha256(skill.read_bytes()).hexdigest()


def _digest_ref(ref: str | None, *, base: Path) -> str | None:
    return _digest_ref_kind(ref, base=base)[0]


def _digest_ref_kind(ref: str | None, *, base: Path) -> tuple[str | None, bytes]:
    """The digest of *ref* and a tag for what it named: ``f`` a file, ``d`` a directory, ``t`` neither."""
    if not ref:
        return None, b"t"
    path = Path(ref.removeprefix("local:"))
    if not path.is_absolute():
        path = base / path
    if _is_file(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), b"f"
    h = hashlib.sha256()
    kind = b"t"
    if _is_dir(path):
        kind = b"d"
        h.update(b"d")
        # POSIX names, ordered by their parts as utils.tree_digest does, so the same tree digests the same on Windows
        for child in sorted((p for p in path.rglob("*") if _is_file(p)), key=lambda p: p.relative_to(path).parts):
            h.update(_field(child.relative_to(path).as_posix()))
            h.update(hashlib.sha256(child.read_bytes()).digest())
    else:
        h.update(b"t" + _field(ref))
    return "sha256:" + h.hexdigest(), kind


def _digest_arm_spec(spec: str, *, base: Path) -> str:
    from dokimi.treatments.specs import content_refs

    h = hashlib.sha256()
    h.update(_field(spec))
    for part, content_ref in content_refs(spec):
        h.update(_field(part))
        digest, kind = _digest_ref_kind(content_ref, base=base)
        h.update(kind + _field(digest or ""))
    return "sha256:" + h.hexdigest()
