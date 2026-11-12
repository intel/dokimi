# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The one verdict every gate in ``dokimi.gate`` returns.

A gate evaluates a list of :class:`Check` and returns one :class:`Verdict`. A check records what it
measured (``value``) against what it allowed (``threshold``), whether it passed, and how a failure is read:

* ``severity``: ``"fail"`` blocks (a failed fail-severity check makes the verdict ``blocking``), ``"warn"``
  is reported, ``"info"`` is an observation. The soft score gate is ``"fail"`` too: it is report-only
  because its caller reads ``passed`` and does not act on ``blocking``.
* ``measured``: False when the artifact did not carry the check's input. Such a check passes without having
  been run and is listed as not measured, never as a pass.
* ``level``: the graded outcome of a gate that grades rather than pass or fail (regression: OK, WARN, CRITICAL).
* ``basis``: what ``value`` is a share of, for a check whose figure depends on it; ``None`` otherwise.

``Verdict.status`` is the vocabulary every gate renders: ``DISABLED``, ``FAIL``, ``WARN``, ``PASS`` or
``NOT MEASURED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Check:
    """One check against one artifact."""

    name: str
    passed: bool
    value: Any = None
    threshold: Any = None
    message: str = ""
    severity: str = "warn"
    measured: bool = True
    basis: str | None = None
    level: str | None = None

    def as_dict(self) -> dict:
        """Serializable form (what the scorecard carries). ``basis`` appears only when it is set."""
        row = {
            "name": self.name,
            "measured": self.measured,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "severity": self.severity,
        }
        if self.basis is not None:
            row["basis"] = self.basis
        return row


@dataclass
class Verdict:
    """All checks one gate ran over one artifact.

    ``enabled`` is False for a gate the spec switched off; it has no checks that could block. ``artifact_ref``
    and ``model`` name the ``(artifact, model)`` cell a novelty verdict describes; ``multirun_std`` is the
    spread of the per-run averages a trust verdict was given.
    """

    checks: list[Check] = field(default_factory=list)
    enabled: bool = True
    artifact_ref: str = ""
    model: str = ""
    multirun_std: float | None = None

    def check(self, name: str) -> Check | None:
        """The first check called ``name``, or ``None``."""
        return next((c for c in self.checks if c.name == name), None)

    def require(self, name: str) -> Check:
        """The first check called ``name``; ``KeyError`` when the gate did not run it."""
        found = self.check(name)
        if found is None:
            raise KeyError(name)
        return found

    @property
    def passed(self) -> bool:
        """Every check passed (a verdict with no checks passed)."""
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        """Fail-severity checks that did not pass."""
        return [c for c in self.checks if not c.passed and c.severity == "fail"]

    @property
    def blocking(self) -> bool:
        """A fail-severity check did not pass."""
        return bool(self.failures)

    @property
    def is_trusted(self) -> bool:
        """Nothing blocking."""
        return not self.blocking

    @property
    def warnings(self) -> list[Check]:
        """Warn-severity checks that did not pass."""
        return [c for c in self.checks if not c.passed and c.severity == "warn"]

    @property
    def notes(self) -> list[Check]:
        """Info-severity checks that did not pass: observations, not problems."""
        return [c for c in self.checks if not c.passed and c.severity == "info"]

    @property
    def not_measured(self) -> list[str]:
        """Checks whose input this run did not produce; they pass without having been run."""
        return [c.name for c in self.checks if not c.measured]

    @property
    def has_warnings(self) -> bool:
        """A warn-severity check did not pass (a regression graded WARN)."""
        return bool(self.warnings)

    @property
    def has_critical(self) -> bool:
        """A fail-severity check did not pass (a regression graded CRITICAL)."""
        return bool(self.failures)

    @property
    def status(self) -> str:
        """``DISABLED``, ``FAIL``, ``WARN``, ``PASS``, or ``NOT MEASURED`` when no check was measured:
        every check passes unmeasured, so ``PASS`` would be read off a run that cleared nothing."""
        if not self.enabled:
            return "DISABLED"
        if self.blocking:
            return "FAIL"
        if self.warnings:
            return "WARN"
        return "PASS" if any(c.measured for c in self.checks) else "NOT MEASURED"

    def as_dict(self) -> dict:
        """Serializable form (the novelty scorecard's shape)."""
        return {
            "artifact_ref": self.artifact_ref,
            "model": self.model,
            "status": self.status,
            "blocking": self.blocking,
            "not_measured": self.not_measured,
            "checks": [c.as_dict() for c in self.checks],
        }
