# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Snapshot comparison logic."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.gate.regression import detect_regressions


def compare_snapshots(base_path: Path, cand_path: Path, spec: dict | None = None) -> dict:
    """Compare two run snapshots and return summary and metric deltas."""
    try:
        base = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        raise ValueError(f"Invalid base snapshot {base_path}: {exc}") from exc

    try:
        cand = json.loads(cand_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid candidate snapshot {cand_path}: {exc}") from exc

    required = {"docs", "score", "coverage", "freshness_lite", "readability"}
    for path, payload in ((base_path, base), (cand_path, cand)):
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            raise ValueError(f"Snapshot missing summary object: {path}")
        missing = required - set(summary.keys())
        # example_pass_rate is optional (may not be present in old snapshots)
        if missing:
            raise ValueError(f"Snapshot {path} missing summary keys: {sorted(missing)}")
        role = "base" if path == base_path else "candidate"
        for key in sorted(required | {"example_pass_rate"}):
            value = summary.get(key)
            if key in summary and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"Invalid {role} snapshot {path}: summary `{key}` must be a number, got {value!r}")

    diff = {
        "docs": cand["summary"]["docs"] - base["summary"]["docs"],
        "score": round(cand["summary"]["score"] - base["summary"]["score"], 4),
        "coverage": round(cand["summary"]["coverage"] - base["summary"]["coverage"], 4),
        "freshness_lite": round(cand["summary"]["freshness_lite"] - base["summary"]["freshness_lite"], 4),
        "readability": round(cand["summary"]["readability"] - base["summary"]["readability"], 4),
    }

    # Include example_pass_rate diff if both snapshots have it
    if "example_pass_rate" in base["summary"] and "example_pass_rate" in cand["summary"]:
        diff["example_pass_rate"] = round(
            cand["summary"]["example_pass_rate"] - base["summary"]["example_pass_rate"], 4
        )

    result = {"base": base["summary"], "candidate": cand["summary"], "diff": diff}

    # Add regression analysis if spec provided
    if spec is not None:
        if not isinstance(spec, dict):
            raise ValueError("spec must be a dict (mapping)")
        regressions = detect_regressions(diff, spec)
        result["regressions"] = {
            "score": {
                "delta": regressions.score_regression.delta,
                "severity": regressions.score_regression.severity,
            },
            "metrics": [
                {"metric": r.metric, "delta": r.delta, "severity": r.severity}
                for r in regressions.metric_regressions
            ],
            "has_warnings": regressions.has_warnings,
            "has_critical": regressions.has_critical,
        }

    return result
