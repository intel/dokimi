# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a format probe returns: a fingerprint and a structure report."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

KIND_EXECUTABLE = "executable"  # has an environment and a verifier
KIND_ANSWER_ONLY = "answer-only"  # prompt/target samples
KIND_TRIGGER = "trigger"  # skill-selection queries
KIND_LOCALIZATION = "localization"  # where-is questions scored on cited locations
KIND_UNKNOWN = "unknown"

FORMAT_UNKNOWN = "unknown"

#: The parts a report says are present or missing, in display order.
PARTS = ("instruction", "environment", "oracle", "verifier", "metadata")

# Labels for what can consume a format today.
BY_HARBOR = "harbor"
BY_DOCKER = "docker-*"
BY_BENCHFLOW = "benchflow"
BY_QUESTIONS = "questions track"
BY_EXPLORE = "explore track"
# Read back by `eval triggers generate --trigger-set`, then run by the skill-catalog: arm.
BY_TRIGGER_SET = "eval triggers generate"
BY_IMPORT = "eval import"
#: Formats ``eval import`` translates (the keys of ``task_formats.imports.ADAPTERS``).
IMPORTABLE = frozenset({"inspect-samples", "inspect-task", "openai-evals", "skill-creator-evals", "promptfoo"})
NONE_YET = "none-yet"

#: A probe at or above this confidence claims the path.
MATCH_THRESHOLD = 0.5
#: Other claims within this margin of the best one are reported as ambiguous.
AMBIGUITY_MARGIN = 0.15


@dataclass(frozen=True)
class Fingerprint:
    """How sure a probe is that a path is its format, and why."""

    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass
class Report:
    """Structure of one detected item (a task directory or a dataset file)."""

    path: Path
    format: str
    kind: str
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    items: int | None = None
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def set_parts(self, parts: dict[str, bool]) -> None:
        """Record which of :data:`PARTS` are present; a part not named is missing."""
        self.present = [p for p in PARTS if parts.get(p)]
        self.missing = [p for p in PARTS if not parts.get(p)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format": self.format,
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "evidence": list(self.evidence),
            "items": self.items,
            "present": list(self.present),
            "missing": list(self.missing),
            "problems": list(self.problems),
            "consumers": list(self.consumers) or [NONE_YET],
            "alternatives": list(self.alternatives),
            "details": _plain(self.details),
        }


def _plain(value: Any) -> Any:
    """*value* as plain JSON: what YAML loads beyond it (dates, sets, bytes, NaN) made JSON."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=str)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


class Probe(Protocol):
    """One format: recognise it (:meth:`fingerprint`) and describe it (:meth:`describe`)."""

    name: str
    #: ``"dir"`` probes look at directories, ``"file"`` probes at files.
    target: str

    def fingerprint(self, source: Any) -> Fingerprint | None: ...

    def describe(self, source: Any) -> Report: ...


def mark_importable(report: Report) -> Report:
    """Name ``eval import`` as a consumer of a format it translates."""
    if report.format in IMPORTABLE and BY_IMPORT not in report.consumers:
        report.consumers.append(BY_IMPORT)
    return report
