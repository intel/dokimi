# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Checks that need every task base, not only the eval tree (contract §2, §3).

A task's ``base`` is usually older than the revision the manifest was read
from. Guidance that was deleted upstream after a base still exists at that
base, and the ``guidance:none`` arm must remove it there, so the globs are
evaluated per base. A base that no longer resolves would fail every run of its
task, so it is an error here.
"""

from __future__ import annotations

from typing import Callable

from .manifest import RepoCheckReport, match_glob
from .source import RepoTree, SourceError


def check_bases(report: RepoCheckReport, open_base: Callable[[str], RepoTree],
                unmatched_at_eval_tree: list[str]) -> None:
    """Resolve each distinct task base and record what the guidance globs match there."""
    manifest = report.manifest
    by_base: dict[str, list[str]] = {}
    failed: set[str] = set()
    matched_somewhere: set[str] = set()
    for task in report.tasks:
        if task.base in by_base or task.base in failed:
            continue
        try:
            files = open_base(task.base).files()
        except SourceError as exc:
            report.errors.append(f"task {task.id}: base {task.base!r} does not resolve in {report.tree}: {exc}")
            failed.add(task.base)
            continue
        found: list[str] = []
        for pattern in manifest.guidance_globs:
            matched = [f for f in files if match_glob(pattern, f) and not manifest.in_eval_dir(f)]
            if matched:
                matched_somewhere.add(pattern)
            found.extend(m for m in matched if m not in found)
        by_base[task.base] = found

    report.guidance_by_base = {base: len(found) for base, found in by_base.items()}
    where = f"any of the {len(by_base)} task base(s) that resolved" if failed else "any task base"
    report.warnings.extend(
        f"guidance glob matches no file at the eval tree or at {where}: {p}"
        for p in unmatched_at_eval_tree if p not in matched_somewhere
    )
    bare = sorted(t.id for t in report.tasks if report.guidance_by_base.get(t.base) == 0)
    if bare:
        report.notes.append(
            f"no guidance file at the base of {len(bare)} task(s), so every guidance:* arm "
            f"is the same workspace there; leave them out of arm comparisons: {', '.join(bare)}"
        )

