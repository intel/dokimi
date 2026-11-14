# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Score threshold gates: ``future.soft_gate`` and ``future.hard_gate`` in a benchmark spec.

Both compare the summary ``score`` against the gate's ``min_score``; they differ only in the
spec key and in what the caller does with a failure. The soft gate is report-only (the static
run records it in its snapshot); on a failed hard gate ``dokimi docs check`` exits 1. Neither
check raises or exits itself.
"""

from __future__ import annotations

from typing import Literal

from dokimi.gate.verdict import Check, Verdict

GateKind = Literal["soft", "hard"]


def check_gate(summary: dict, spec: dict, kind: GateKind) -> Verdict:
    """Check ``summary["score"]`` against ``spec["future"]["<kind>_gate"]``.

    A missing section reads as disabled; ``min_score`` defaults to 0.0, the score to 0.0. The one check is
    named ``score``: its ``value`` is the score and its ``threshold`` the floor, also when the gate is disabled.
    """
    gate_cfg = spec.get("future", {}).get(f"{kind}_gate", {})
    enabled = bool(gate_cfg.get("enabled", False))
    min_score = float(gate_cfg.get("min_score", 0.0))
    actual = float(summary.get("score", 0.0))

    return Verdict(
        enabled=enabled,
        checks=[Check(
            name="score",
            passed=actual >= min_score if enabled else True,
            value=actual,
            threshold=min_score,
            message=f"score {actual:.4f} against the {kind} gate's floor {min_score:.4f}",
            severity="fail",
            measured=enabled,
        )],
    )
