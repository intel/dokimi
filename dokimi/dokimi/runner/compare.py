# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Snapshot comparison logic."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from dokimi.gate.regression import detect_regressions


_REQUIRED_SUMMARY_KEYS = {"docs", "score", "coverage", "freshness_lite", "readability"}


def _load_snapshot(path: Path, role: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        raise ValueError(f"Invalid {role} snapshot {path}: {exc}") from exc


def _check_summary(path: Path, payload: object, role: str) -> None:
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        raise ValueError(f"Snapshot missing summary object: {path}")
    missing = _REQUIRED_SUMMARY_KEYS - set(summary.keys())
    # example_pass_rate is optional (may not be present in old snapshots)
    if missing:
        raise ValueError(f"Snapshot {path} missing summary keys: {sorted(missing)}")
    for key in sorted(_REQUIRED_SUMMARY_KEYS | {"example_pass_rate"}):
        value = summary.get(key)
        if key in summary and not _valid_number(key, value):
            kind = "an integer" if key == "docs" else "a finite number"
            raise ValueError(f"Invalid {role} snapshot {path}: summary `{key}` must be {kind}, got {value!r}")


def _valid_number(key: str, value: object) -> bool:
    if isinstance(value, bool):
        return False
    if key == "docs":
        return isinstance(value, int)
    return isinstance(value, (int, float)) and math.isfinite(value)


def compare_snapshots(base_path: Path, cand_path: Path, spec: dict | None = None) -> dict:
    """Compare two run snapshots and return summary and metric deltas."""
    base = _load_snapshot(base_path, "base")
    cand = _load_snapshot(cand_path, "candidate")

    for path, payload in ((base_path, base), (cand_path, cand)):
        _check_summary(path, payload, "base" if path == base_path else "candidate")

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
