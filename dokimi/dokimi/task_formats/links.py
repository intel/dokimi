# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read a dataset file as the format the config naming it says it is.

Some dataset files cannot say what they are: an Inspect ``biology_qa.jsonl`` holds
``question``/``answer`` (its ``@task`` file maps them with ``FieldSpec``), an OpenAI
evals model-graded ``samples.jsonl`` holds only ``input`` (its registry spec names
the grader). A probe that recognises the config lists the files it names in
``details["references"]`` (:func:`dokimi.task_formats.configs.reference`); after a
tree scan, :func:`resolve` re-reads each named file found in the scan with that
format's probe and the config's hint, and says which file named it. A named file
that is a git-LFS pointer keeps its report and gains ``expected_format``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dokimi.task_formats.model import Probe, Report, mark_importable
from dokimi.task_formats.pointers import FORMAT_LFS_POINTER
from dokimi.task_formats.source import FileSource

#: Confidence of a format a config in the scan assigns to a file it names.
REFERENCED_CONFIDENCE = 0.9

Entry = tuple[Path, "Report | None"]


def _key(path: Path) -> str:
    return str(Path(path).resolve())


def _targets(ref: dict[str, Any], by_path: dict[str, int], by_name: dict[str, list[int]]) -> list[int]:
    if ref.get("match") == "name":
        return list(by_name.get(Path(ref["path"]).name, []))
    try:
        index = by_path.get(_key(Path(ref["path"])))
    except (OSError, ValueError):  # a NUL byte or an unresolvable path: names nothing scanned
        return []
    return [] if index is None else [index]


def _hinted(path: Path, ref: dict[str, Any], referrer: Path, probe: Probe) -> Report:
    hint = {k: v for k, v in ref.items() if k not in ("path", "format", "match")}
    hint["referenced_by"] = str(referrer)
    report = mark_importable(probe.describe(FileSource(path, hint)))
    report.confidence = REFERENCED_CONFIDENCE
    report.evidence = [f"named by {referrer.name}"]
    if hint.get("fields"):
        spec = ", ".join(f"{k}={v}" for k, v in sorted(hint["fields"].items()))
        report.evidence.append(f"FieldSpec({spec})")
    return report


def _relabel(entries: list[Entry], index: int, ref: dict[str, Any], referrer: Path,
             probes: Mapping[str, Probe]) -> None:
    path, current = entries[index]
    if current is not None and current.format == FORMAT_LFS_POINTER:
        current.details["expected_format"] = ref["format"]
        current.evidence.append(f"named by {referrer.name} as {ref['format']}")
        return
    probe = probes.get(ref["format"])
    if probe is None or (current is not None and current.confidence >= 1.0
                         and current.format != ref["format"]):
        return
    report = _hinted(path, ref, referrer, probe)
    if current is not None and current.format != ref["format"]:
        report.alternatives = [{"format": current.format, "confidence": round(current.confidence, 2),
                                "evidence": list(current.evidence)}]
    entries[index] = (path, report)


def resolve(entries: list[Entry], probes: Mapping[str, Probe],
            unread: Mapping[Path, Report] | None = None) -> list[Report]:
    """The scan's reports, each file a config names read as that config says.

    *entries* is every file and task directory the scan looked at, in order, with its
    report or ``None`` when no probe claimed it; *probes* maps a format to its probe.
    An unclaimed file no config names is dropped, unless *unread* holds a report for
    it (a file that could not be read or parsed: it may be a task, and says why not).
    """
    unread = unread or {}
    by_path = {_key(p): i for i, (p, _) in enumerate(entries)}
    by_name: dict[str, list[int]] = {}
    for i, (p, _) in enumerate(entries):
        by_name.setdefault(p.name, []).append(i)
    referrers = [(p, r) for p, r in entries if r is not None and r.details.get("references")]
    for referrer, report in referrers:
        for ref in report.details["references"]:
            for index in _targets(ref, by_path, by_name):
                _relabel(entries, index, ref, referrer, probes)
    return [r if r is not None else unread[p] for p, r in entries if r is not None or p in unread]
