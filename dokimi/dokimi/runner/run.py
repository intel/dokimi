# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Benchmark run orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dokimi.ingest.chunker import chunk_text
from dokimi.ingest.loader import discover_markdown, load_docs
from dokimi.metrics import coverage, freshness_lite, readability
from dokimi.metrics.example_runner import ExampleResult, ExecutionPolicy, score_examples
from dokimi.gate.score_gate import check_gate
from dokimi.runner.spec import load_spec, select_docs
from dokimi.runner.manifest import build_run_manifest


@dataclass
class DocMetrics:
    """Per-document metric bundle."""

    path: str
    chunks: int
    coverage: float
    freshness_lite: float
    readability: float
    example_pass_rate: float
    score: float


def _weighted_score(doc: dict, weights: dict[str, float], active_metrics: list[str]) -> float:
    """Compute normalized weighted score across active metrics."""
    total_weight = sum(weights[m] for m in active_metrics)
    if total_weight == 0:
        return 0.0
    raw = sum(doc[m] * weights[m] for m in active_metrics)
    return round(raw / total_weight, 4)


def _load_spec(spec_path: Path) -> dict:
    """Load a benchmark spec YAML with explicit, descriptive failures.

    Full v1 specs (those declaring ``version``/``golden_manifest``) are
    validated against ``dokimi/runner/spec.schema.json``. Minimal/legacy specs
    skip schema validation but still get the lightweight presence checks below.
    """
    data = load_spec(spec_path)

    missing: list[str] = []
    if "weights" not in data:
        missing.append("weights")
    if "metrics" not in data or not isinstance(data.get("metrics"), dict):
        missing.append("metrics")
    else:
        metrics = data["metrics"]
        freshness_cfg = metrics.get("freshness_lite")
        if not isinstance(freshness_cfg, dict) or "max_age_days" not in freshness_cfg:
            missing.append("metrics.freshness_lite.max_age_days")
        readability_cfg = metrics.get("readability")
        if not isinstance(readability_cfg, dict) or "grade_max" not in readability_cfg:
            missing.append("metrics.readability.grade_max")

    if missing:
        raise RuntimeError(f"Spec missing required fields ({', '.join(missing)}): {spec_path}")

    return data


load_benchmark_spec = _load_spec


class DocLoadError(RuntimeError):
    """A document the run selected could not be read: the run refuses rather than score around it."""


def _load_found_docs(docs: list[Path]) -> dict[str, str]:
    """``load_docs`` over the selected docs; a doc that does not load is a clean error, not a traceback."""
    try:
        loaded = load_docs(docs)
    except OSError as exc:
        raise DocLoadError(f"could not read {exc.filename or 'a document'}: {exc.strerror or exc} "
                           f"({len(docs)} document(s) selected)") from None
    unloaded = [str(p) for p in docs if str(p) not in loaded]
    if unloaded:
        raise DocLoadError(f"{len(unloaded)} of {len(docs)} selected document(s) did not load: "
                           + ", ".join(unloaded[:5]) + (" ..." if len(unloaded) > 5 else ""))
    return loaded


def run_benchmark(root: Path, spec_path: Path) -> dict:
    """Run benchmark on markdown docs and return snapshot payload."""
    spec = _load_spec(spec_path)
    weights = spec["weights"]
    example_cfg = spec["metrics"].get("example_pass_rate", {})
    example_enabled = bool(example_cfg.get("enabled", False))
    example_policy = ExecutionPolicy.from_config(example_cfg)

    active_metrics = ["coverage", "freshness_lite", "readability"]
    if example_enabled:
        active_metrics.append("example_pass_rate")

    manifest = spec.get("golden_manifest")
    if manifest:
        docs = select_docs(root, manifest)
    else:
        # Legacy/minimal specs without a manifest: discover all markdown under docs/.
        docs = discover_markdown(root / "docs")
    loaded = _load_found_docs(docs)

    # Collect per-doc metrics and cached example results
    rows: list[dict] = []
    cached_example_results: list[list[ExampleResult]] = []

    for p in docs:
        text = loaded[str(p)]
        row: dict = {
            "path": str(p.relative_to(root)),
            "chunks": len(chunk_text(text)),
            "coverage": coverage.score(text),
            "freshness_lite": freshness_lite.score(p, spec["metrics"]["freshness_lite"]["max_age_days"]),
            "readability": readability.score(text, spec["metrics"]["readability"]["grade_max"]),
        }

        if example_enabled:
            ex_score, ex_results = score_examples(p, example_policy)
            row["example_pass_rate"] = ex_score
            cached_example_results.append(ex_results)
        else:
            row["example_pass_rate"] = 0.0
            cached_example_results.append([])

        row["score"] = _weighted_score(row, weights, active_metrics)
        rows.append(row)

    metric_fields = ["coverage", "freshness_lite", "readability"]
    if example_enabled:
        metric_fields.append("example_pass_rate")

    agg: dict = {
        m: round(sum(r[m] for r in rows) / max(1, len(rows)), 4)
        for m in metric_fields
    }
    total_score = round(sum(r["score"] for r in rows) / max(1, len(rows)), 4)

    docs_out = []
    for row, ex_results in zip(rows, cached_example_results, strict=True):
        d = {
            "path": row["path"],
            "chunks": row["chunks"],
            "coverage": row["coverage"],
            "freshness_lite": row["freshness_lite"],
            "readability": row["readability"],
            "example_pass_rate": row["example_pass_rate"],
            "score": row["score"],
        }
        if example_enabled:
            d["example_results"] = [
                {"index": r.index, "lang": r.lang, "passed": r.passed,
                 "status": r.status, "error": r.error}
                for r in ex_results
            ]
        docs_out.append(d)

    gate_result = check_gate({"score": total_score}, spec, "soft")

    return {
        "summary": {"docs": len(rows), "score": total_score, **agg},
        "docs": docs_out,
        "gate": {
            "soft": {
                "enabled": gate_result.enabled,
                "passed": gate_result.passed,
                "min_score": gate_result.min_score,
            }
        },
        "run_manifest": build_run_manifest(spec_path=spec_path, doc_paths=docs),
    }


def save_snapshot(data: dict, out_path: Path) -> None:
    """Persist run snapshot to JSON file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
