# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a format probe returns: a fingerprint and a structure report."""

from __future__ import annotations

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
            "details": dict(self.details),
        }


class Probe(Protocol):
    """One format: recognise it (:meth:`fingerprint`) and describe it (:meth:`describe`)."""

    name: str
    #: ``"dir"`` probes look at directories, ``"file"`` probes at files.
    target: str

    def fingerprint(self, source: Any) -> Fingerprint | None: ...

    def describe(self, source: Any) -> Report: ...
