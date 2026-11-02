# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Roll up explicit matrix-cell arms artifacts."""

from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs


def build_matrix_rollup(
    *,
    library_name: str,
    cell_artifacts: list[tuple[str, Path, dict[str, Any]]],
    out_dir: Path,
    compute_plugin_deltas: bool = True,
) -> dict[str, Any]:
    """Build a rollup artifact from per-cell ``arms.v1`` artifacts."""
    cells = [
        {
            "matrix_cell": name,
            "artifact": str(path),
            "model": data.get("model"),
            "provider": data.get("provider"),
            "harness": data.get("harness"),
            "plugin_set": data.get("plugin_set", "none"),
            "plugin_set_id": data.get("plugin_set_id"),
            "baseline_arm": data.get("baseline_arm"),
            "summary": data.get("summary", {}),
            "cost_summary": data.get("cost_summary", {}),
        }
        for name, path, data in cell_artifacts
    ]
    warnings: list[str] = []
    plugin_deltas: list[dict[str, Any]] = []

    if compute_plugin_deltas:
        plugin_deltas, warnings = _paired_plugin_deltas(cell_artifacts, out_dir)

    return {
        "schema_version": "matrix_rollup.v1",
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "library_name": library_name,
        "total_cells": len(cells),
        "cells": cells,
        "plugin_deltas": plugin_deltas,
        "warnings": warnings,
    }


def _paired_plugin_deltas(
    cell_artifacts: list[tuple[str, Path, dict[str, Any]]],
    out_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    baselines: dict[tuple[Any, ...], tuple[str, Path, dict[str, Any]]] = {}
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    warnings: list[str] = []

    for item in cell_artifacts:
        _, _, data = item
        key = _pair_key(data)
        if data.get("plugin_set", "none") == "none":
            if key in baselines:
                warnings.append(f"multiple no-plugin baselines for pair key {key!r}; using first")
            else:
                baselines[key] = item
        else:
            candidates.append(item)

    deltas: list[dict[str, Any]] = []
    for plugin_name, _, plugin_data in candidates:
        baseline = baselines.get(_pair_key(plugin_data))
        if baseline is None:
            warnings.append(f"no no-plugin baseline found for matrix cell {plugin_name!r}")
            continue
        baseline_name, _, baseline_data = baseline
        try:
            delta = compare_plugin_runs(baseline_data, plugin_data)
        except PluginDeltaError as exc:
            warnings.append(
                f"could not compute plugin delta for {baseline_name!r} -> {plugin_name!r}: {exc}"
            )
            continue

        pair_suffix = sha256(f"{baseline_name}\0{plugin_name}".encode("utf-8")).hexdigest()[:8]
        delta_path = out_dir / (
            f"plugin-delta-{_safe_name(baseline_name)}-to-"
            f"{_safe_name(plugin_name)}-{pair_suffix}.json"
        )
        deltas.append({
            "baseline_cell": baseline_name,
            "plugin_cell": plugin_name,
            "artifact": str(delta_path),
            "plugin_set": delta.get("plugin_set"),
            "plugin_set_id": delta.get("plugin_set_id"),
            "score_deltas": delta.get("score_deltas", {}),
            "cost_deltas": delta.get("cost_deltas", {}),
            "answer_text_deltas": delta.get("answer_text_deltas", {}),
            "_artifact_payload": delta,
        })
    return deltas, warnings


def strip_internal_payloads(rollup: dict[str, Any]) -> dict[str, Any]:
    """Return a copy suitable for saving after plugin-delta payloads were written."""
    out = dict(rollup)
    out["plugin_deltas"] = [
        {k: v for k, v in row.items() if k != "_artifact_payload"}
        for row in rollup.get("plugin_deltas", [])
    ]
    return out


def _pair_key(data: dict[str, Any]) -> tuple[Any, ...]:
    return (
        data.get("library_name"),
        data.get("model"),
        data.get("provider"),
        data.get("harness"),
        tuple(data.get("arms") or []),
        data.get("baseline_arm"),
        data.get("total_questions"),
        (data.get("run_config") or {}).get("session_turns"),
        (data.get("run_config") or {}).get("turn_reminder"),
    )


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "-" for c in name)
    return safe.strip("-") or "cell"
