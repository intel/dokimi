# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A git-LFS pointer: a dataset file whose content was never fetched.

A clone made without ``git lfs pull`` (or a ``git archive`` of an LFS repository)
holds a three-line stub where the data should be. Every other probe sees it as
malformed JSON or as nothing; this one names it, so a report says "fetch the
content" instead of "unknown" or "not valid JSONL".
"""

from __future__ import annotations

from dokimi.task_formats.model import KIND_UNKNOWN, Fingerprint, Report
from dokimi.task_formats.source import FileSource

FORMAT_LFS_POINTER = "git-lfs-pointer"


class LfsPointerProbe:
    name = FORMAT_LFS_POINTER
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        return Fingerprint(1.0, ("git-lfs spec v1 pointer",)) if source.lfs else None

    def describe(self, source: FileSource) -> Report:
        lfs = source.lfs or {}
        report = Report(source.path, self.name, KIND_UNKNOWN)
        report.details.update(lfs)
        report.problems.append(
            f"git-LFS pointer: the {lfs.get('size', '?')}-byte content was not fetched "
            "(run `git lfs pull`, then inspect again)")
        return report
