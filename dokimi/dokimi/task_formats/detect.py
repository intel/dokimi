# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The probe registry and the scan: which eval-task format is each path?

A directory a directory probe claims (a task) is one item and is not descended
into; any other directory is walked, and each data file in it is offered to the
file probes. When two probes claim a path about equally, the best is reported
and the others are listed as alternatives with their evidence. A path nothing
claims is reported as ``unknown`` with what it holds, never raised.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from dokimi.task_formats.configs import InspectTaskProbe, PromptfooProbe
from dokimi.task_formats.datasets import (
    InspectSamplesProbe, OpenAIEvalsProbe, QuestionsProbe, SkillCreatorEvalsProbe,
    SkillCreatorTriggerProbe, SkillReachProbe, SweBenchProbe,
)
from dokimi.task_formats.dirs import HarborProbe, TaskMdProbe, TerminalBenchV1Probe
from dokimi.task_formats.model import (
    AMBIGUITY_MARGIN, FORMAT_UNKNOWN, KIND_UNKNOWN, MATCH_THRESHOLD, Fingerprint, Probe, Report,
)
from dokimi.task_formats.source import FileSource

DIR_PROBES: tuple[Probe, ...] = (HarborProbe(), TaskMdProbe(), TerminalBenchV1Probe())
FILE_PROBES: tuple[Probe, ...] = (
    SweBenchProbe(), InspectSamplesProbe(), OpenAIEvalsProbe(), SkillCreatorEvalsProbe(),
    SkillCreatorTriggerProbe(), SkillReachProbe(), QuestionsProbe(), PromptfooProbe(),
    InspectTaskProbe(),
)
PROBES: tuple[Probe, ...] = DIR_PROBES + FILE_PROBES

#: Directories a scan never enters.
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
                       ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
#: Suffixes a scan offers to the file probes.
SCAN_SUFFIXES = frozenset({".json", ".jsonl", ".yaml", ".yml", ".py"})
#: A scan stops after this many files and says so.
MAX_SCAN_FILES = 20000


def _claims(probes: Sequence[Probe], subject: Any) -> list[tuple[Fingerprint, Probe]]:
    found = []
    for probe in probes:
        fp = probe.fingerprint(subject)
        if fp is not None and fp.confidence >= MATCH_THRESHOLD:
            found.append((fp, probe))
    return sorted(found, key=lambda c: -c[0].confidence)


def _evidence(fp: Fingerprint) -> list[str]:
    return [e for e in fp.evidence if e]


def detect(path: Path, probes: Sequence[Probe] | None = None) -> Report | None:
    """The report of the best probe claiming *path*, or ``None`` if none does."""
    path = Path(path)
    if probes is None:
        probes = DIR_PROBES if path.is_dir() else FILE_PROBES
    subject: Any = path if path.is_dir() else FileSource(path)
    claims = _claims(probes, subject)
    if not claims:
        return None
    (best_fp, best), rest = claims[0], claims[1:]
    report = best.describe(subject)
    report.confidence = best_fp.confidence
    report.evidence = _evidence(best_fp)
    report.alternatives = [
        {"format": p.name, "confidence": round(fp.confidence, 2), "evidence": _evidence(fp)}
        for fp, p in rest if round(best_fp.confidence - fp.confidence, 6) <= AMBIGUITY_MARGIN
    ]
    return report


def inspect_path(path: Path) -> list[Report]:
    """Every item under *path* (a file, a task directory, or a tree), or one ``unknown``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no such file or directory: {path}")
    if path.is_file():
        return [detect(path) or unknown(path)]
    scan = _Scan()
    reports = list(scan.walk(path))
    if not reports:
        reports = [unknown(path)]
    if scan.truncated:
        reports[-1].problems.append(f"scan stopped after {MAX_SCAN_FILES} files")
    return reports


class _Scan:
    def __init__(self) -> None:
        self.files = 0
        self.truncated = False

    def walk(self, directory: Path) -> Iterator[Report]:
        task = detect(directory, DIR_PROBES)
        if task is not None:
            yield task
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if self.truncated:
                return
            yield from self._child(child)

    def _child(self, child: Path) -> Iterator[Report]:
        if child.is_symlink():
            return
        if child.is_dir():
            if child.name not in SKIP_DIRS and not child.name.startswith("."):
                yield from self.walk(child)
            return
        if child.suffix.lower() not in SCAN_SUFFIXES:
            return
        self.files += 1
        if self.files > MAX_SCAN_FILES:
            self.truncated = True
            return
        report = detect(child, FILE_PROBES)
        if report is not None:
            yield report


# --- unknown -----------------------------------------------------------------

_PROMPT_NAMES = re.compile(r"^(instruction|prompt|task|problem|question)s?[\w-]*\.(md|txt|ya?ml|json)$", re.I)
_VERIFIER_NAMES = re.compile(r"^(test|tests|verify|verifier|run-tests|check|grade|grader)[\w-]*(\.\w+)?$", re.I)
_LISTED = 30


def unknown(path: Path) -> Report:
    """Best-effort structure of a path no probe claims."""
    report = Report(path, FORMAT_UNKNOWN, KIND_UNKNOWN)
    if path.is_dir():
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        report.details["files"] = names[:_LISTED]
        report.details["prompt_candidates"] = [n for n in names if _PROMPT_NAMES.match(n)]
        report.details["verifier_candidates"] = [n for n in names if _VERIFIER_NAMES.match(n.rstrip("/"))]
        return report
    source = FileSource(path)
    data = source.data
    report.problems.extend(source.problems)
    if isinstance(data, dict):
        report.details["keys"] = sorted(map(str, data))[:_LISTED]
    elif source.rows:
        report.items = len(source.rows)
        report.details["row_keys"] = sorted(map(str, source.rows[0]))[:_LISTED]
    return report
