# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Score threshold gates: ``future.soft_gate`` and ``future.hard_gate`` in a benchmark spec.

Both compare the summary ``score`` against the gate's ``min_score``; they differ only in the
spec key and in what the caller does with a failure. The soft gate is report-only (the static
run records it in its snapshot); on a failed hard gate ``dokimi run`` exits 1. Neither
check raises or exits itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GateKind = Literal["soft", "hard"]


@dataclass
class GateResult:
    """Outcome of one score gate. A disabled gate always passes."""

    enabled: bool
    min_score: float
    actual_score: float
    passed: bool

    @property
    def status(self) -> str:
        """Human-readable status."""
        if not self.enabled:
            return "DISABLED"
        return "PASS" if self.passed else "FAIL"


def check_gate(summary: dict, spec: dict, kind: GateKind) -> GateResult:
    """Check ``summary["score"]`` against ``spec["future"]["<kind>_gate"]``.

    A missing section reads as disabled; ``min_score`` defaults to 0.0, the score to 0.0.
    """
    gate_cfg = spec.get("future", {}).get(f"{kind}_gate", {})
    enabled = bool(gate_cfg.get("enabled", False))
    min_score = float(gate_cfg.get("min_score", 0.0))
    actual = float(summary.get("score", 0.0))

    return GateResult(
        enabled=enabled,
        min_score=min_score,
        actual_score=actual,
        passed=actual >= min_score if enabled else True,
    )
