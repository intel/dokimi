# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for runner/run.py."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dokimi.runner.run import _weighted_score, _load_spec, run_benchmark, save_snapshot


def test_weighted_score_basic():
    doc = {"coverage": 0.8, "freshness_lite": 0.6, "readability": 1.0}
    weights = {"coverage": 0.5, "freshness_lite": 0.25, "readability": 0.25}
    s = _weighted_score(doc, weights, ["coverage", "freshness_lite", "readability"])
    assert s == 0.8


def test_weighted_score_zero_total_weight():
    doc = {"coverage": 0.8}
    weights = {"coverage": 0.0}
    assert _weighted_score(doc, weights, ["coverage"]) == 0.0


def test_load_spec_valid(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(
        "weights:\n  coverage: 0.4\n  freshness_lite: 0.3\n  readability: 0.3\n"
        "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n"
    )
    data = _load_spec(p)
    assert "weights" in data


def test_load_spec_invalid_yaml(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("weights: [")
    with pytest.raises(RuntimeError, match="Invalid YAML"):
        _load_spec(p)


def test_load_spec_missing_fields(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text("weights: {}\nmetrics: {}\n")
    with pytest.raises(RuntimeError, match="missing required fields"):
        _load_spec(p)


def test_run_benchmark_basic(tmp_path):
    root = tmp_path
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "weights:\n  coverage: 0.4\n  freshness_lite: 0.3\n  readability: 0.3\n"
        "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n"
    )

    with patch("dokimi.runner.run.discover_markdown", return_value=[root / "docs/a.md"]), \
         patch("dokimi.runner.run.load_docs", return_value={str(root / "docs/a.md"): "hello world"}), \
         patch("dokimi.runner.run.chunk_text", return_value=["c1", "c2"]), \
         patch("dokimi.runner.run.coverage.score", return_value=0.8), \
         patch("dokimi.runner.run.freshness_lite.score", return_value=0.9), \
         patch("dokimi.runner.run.readability.score", return_value=0.7), \
         patch("dokimi.runner.run.check_gate") as gate:
        gate.return_value = type("G", (), {"enabled": True, "passed": True, "min_score": 0.6})()
        out = run_benchmark(root, spec)

    assert out["summary"]["docs"] == 1
    assert "score" in out["summary"]
    assert out["docs"][0]["chunks"] == 2
    assert out["gate"]["soft"]["enabled"] is True


def test_run_benchmark_with_examples(tmp_path):
    root = tmp_path
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "weights:\n  coverage: 0.25\n  freshness_lite: 0.25\n  readability: 0.25\n  example_pass_rate: 0.25\n"
        "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n"
        "  example_pass_rate:\n    enabled: true\n    timeout: 5\n"
    )

    ex_result = type("Ex", (), {"index": 0, "lang": "python", "passed": True, "status": "passed", "error": None})

    with patch("dokimi.runner.run.discover_markdown", return_value=[root / "docs/a.md"]), \
         patch("dokimi.runner.run.load_docs", return_value={str(root / "docs/a.md"): "hello world"}), \
         patch("dokimi.runner.run.chunk_text", return_value=["c1"]), \
         patch("dokimi.runner.run.coverage.score", return_value=0.8), \
         patch("dokimi.runner.run.freshness_lite.score", return_value=0.9), \
         patch("dokimi.runner.run.readability.score", return_value=0.7), \
         patch("dokimi.runner.run.score_examples", return_value=(1.0, [ex_result()])), \
         patch("dokimi.runner.run.check_gate") as gate:
        gate.return_value = type("G", (), {"enabled": True, "passed": True, "min_score": 0.6})()
        out = run_benchmark(root, spec)

    assert "example_pass_rate" in out["summary"]
    assert "example_results" in out["docs"][0]


def test_save_snapshot(tmp_path):
    out = tmp_path / "out" / "snap.json"
    save_snapshot({"summary": {"docs": 0}}, out)
    assert out.exists()
    assert json.loads(out.read_text())["summary"]["docs"] == 0
