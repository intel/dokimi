# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for runner/compare.py, report/markdown_report.py, report/json_report.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dokimi.runner.compare import compare_snapshots
from dokimi.report.markdown_report import write_run_report, write_compare_report
from dokimi.report.json_report import write_json


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _snapshot(score=0.8, coverage=0.7, freshness=0.9, readability=0.6, docs=10):
    return {
        "summary": {
            "docs": docs,
            "score": score,
            "coverage": coverage,
            "freshness_lite": freshness,
            "readability": readability,
        },
        "docs": [],
    }


def _write_snapshot(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── compare_snapshots ─────────────────────────────────────────────────────────

def test_compare_snapshots_basic_diff(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot(score=0.7))
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot(score=0.8))
    result = compare_snapshots(base, cand)
    assert abs(result["diff"]["score"] - 0.1) < 1e-4
    assert result["diff"]["docs"] == 0


def test_compare_snapshots_negative_diff(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot(score=0.9, coverage=0.8))
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot(score=0.7, coverage=0.6))
    result = compare_snapshots(base, cand)
    assert result["diff"]["score"] < 0
    assert result["diff"]["coverage"] < 0


def test_compare_snapshots_docs_count_diff(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot(docs=5))
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot(docs=8))
    result = compare_snapshots(base, cand)
    assert result["diff"]["docs"] == 3


def test_compare_snapshots_includes_base_and_candidate(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot(score=0.7))
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot(score=0.8))
    result = compare_snapshots(base, cand)
    assert "base" in result
    assert "candidate" in result
    assert result["base"]["score"] == pytest.approx(0.7)
    assert result["candidate"]["score"] == pytest.approx(0.8)


def test_compare_snapshots_with_spec_no_regressions(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot(score=0.7))
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot(score=0.8))
    spec = {"score": {"warn": -0.05, "critical": -0.1}}
    with patch("dokimi.runner.compare.detect_regressions") as mock_dr:
        mock_dr.return_value = type("R", (), {
            "score_regression": type("S", (), {"delta": 0.1, "severity": "OK"})(),
            "metric_regressions": [],
            "has_warnings": False,
            "has_critical": False,
        })()
        result = compare_snapshots(base, cand, spec=spec)
    assert "regressions" in result
    assert result["regressions"]["has_critical"] is False


def test_compare_snapshots_missing_file_raises(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        compare_snapshots(tmp_path / "missing.json", tmp_path / "also_missing.json")


def test_compare_snapshots_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    good = _write_snapshot(tmp_path / "good.json", _snapshot())
    with pytest.raises(ValueError, match="Invalid"):
        compare_snapshots(bad, good)


def test_compare_snapshots_missing_summary_raises(tmp_path):
    no_summary = tmp_path / "nosummary.json"
    no_summary.write_text(json.dumps({"docs": []}), encoding="utf-8")
    good = _write_snapshot(tmp_path / "good.json", _snapshot())
    with pytest.raises(ValueError, match="summary"):
        compare_snapshots(no_summary, good)


def test_compare_snapshots_missing_summary_keys_raises(tmp_path):
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"summary": {"docs": 1, "score": 0.5}}), encoding="utf-8")
    good = _write_snapshot(tmp_path / "good.json", _snapshot())
    with pytest.raises(ValueError, match="missing summary keys"):
        compare_snapshots(incomplete, good)


def test_compare_snapshots_optional_example_pass_rate(tmp_path):
    s1 = _snapshot()
    s1["summary"]["example_pass_rate"] = 0.9
    s2 = _snapshot()
    s2["summary"]["example_pass_rate"] = 0.85
    base = _write_snapshot(tmp_path / "base.json", s1)
    cand = _write_snapshot(tmp_path / "cand.json", s2)
    result = compare_snapshots(base, cand)
    assert "example_pass_rate" in result["diff"]
    assert abs(result["diff"]["example_pass_rate"] - (-0.05)) < 1e-4


def test_compare_snapshots_no_example_pass_rate_when_missing(tmp_path):
    base = _write_snapshot(tmp_path / "base.json", _snapshot())
    cand = _write_snapshot(tmp_path / "cand.json", _snapshot())
    result = compare_snapshots(base, cand)
    assert "example_pass_rate" not in result["diff"]


# ── write_run_report ──────────────────────────────────────────────────────────

def _run_data(score=0.75, with_gate=False, gate_passed=True):
    data = {
        "summary": {
            "docs": 3, "score": score, "coverage": 0.8,
            "freshness_lite": 0.9, "readability": 0.7,
        },
        "docs": [
            {"path": "doc1.md", "score": 0.8, "coverage": 0.75,
             "freshness_lite": 0.9, "readability": 0.7, "chunks": 5},
        ],
    }
    if with_gate:
        data["gate"] = {"soft": {"enabled": True, "passed": gate_passed,
                                  "min_score": 0.7}}
    return data


def test_write_run_report_creates_file(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(), out)
    assert out.exists()


def test_write_run_report_contains_summary(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(score=0.75), out)
    text = out.read_text(encoding="utf-8")
    assert "0.7500" in text
    assert "## Summary" in text


def test_write_run_report_contains_docs(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(), out)
    text = out.read_text(encoding="utf-8")
    assert "doc1.md" in text
    assert "## Docs" in text


def test_write_run_report_gate_pass(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(with_gate=True, gate_passed=True), out)
    text = out.read_text(encoding="utf-8")
    assert "PASS" in text
    assert "✅" in text


def test_write_run_report_gate_fail(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(with_gate=True, gate_passed=False), out)
    text = out.read_text(encoding="utf-8")
    assert "FAIL" in text
    assert "❌" in text


def test_write_run_report_no_gate_section_when_absent(tmp_path):
    out = tmp_path / "report.md"
    write_run_report(_run_data(with_gate=False), out)
    assert "Soft Gate" not in out.read_text(encoding="utf-8")


def test_write_run_report_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deep" / "report.md"
    write_run_report(_run_data(), out)
    assert out.exists()


# ── write_compare_report ──────────────────────────────────────────────────────

def _compare_data(with_regressions=False, has_critical=False, has_warnings=False):
    data = {
        "diff": {"docs": 1, "score": 0.05, "coverage": -0.02,
                 "freshness_lite": 0.01, "readability": 0.0},
        "base": _snapshot()["summary"],
        "candidate": _snapshot(score=0.85)["summary"],
    }
    if with_regressions:
        data["regressions"] = {
            "has_critical": has_critical,
            "has_warnings": has_warnings,
            "score": {"delta": -0.15 if has_critical else 0.05, "severity": "CRITICAL" if has_critical else "OK"},
            "metrics": [],
        }
    return data


def test_write_compare_report_creates_file(tmp_path):
    out = tmp_path / "compare.md"
    write_compare_report(_compare_data(), out)
    assert out.exists()


def test_write_compare_report_contains_diff(tmp_path):
    out = tmp_path / "compare.md"
    write_compare_report(_compare_data(), out)
    text = out.read_text(encoding="utf-8")
    assert "## Diff" in text
    assert "+0.0500" in text or "+1" in text


def test_write_compare_report_no_regressions(tmp_path):
    out = tmp_path / "compare.md"
    write_compare_report(_compare_data(with_regressions=True), out)
    text = out.read_text(encoding="utf-8")
    assert "No regressions" in text


def test_write_compare_report_with_no_scored_doc_is_not_no_regressions(tmp_path):
    # Two 0-doc snapshots diff to 0 everywhere: that read "No regressions".
    out = tmp_path / "compare.md"
    data = _compare_data(with_regressions=True)
    data["base"], data["candidate"] = _snapshot(docs=0)["summary"], _snapshot(docs=0)["summary"]
    write_compare_report(data, out)
    text = out.read_text(encoding="utf-8")
    assert "Nothing to compare (0 scored)**: no document in base and candidate" in text
    assert "No regressions" not in text


def test_write_compare_report_empty_candidate_keeps_the_critical_headline(tmp_path):
    # A candidate that lost every document is a regression; "Nothing to compare" must not replace the headline.
    out = tmp_path / "compare.md"
    data = _compare_data(with_regressions=True, has_critical=True)
    data["candidate"] = _snapshot(docs=0, score=0.0)["summary"]
    write_compare_report(data, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "🔴 **CRITICAL regressions detected**" in lines
    assert any("Nothing to compare (0 scored)**: no document in candidate" in ln for ln in lines)


def test_write_compare_report_critical_regressions(tmp_path):
    out = tmp_path / "compare.md"
    write_compare_report(_compare_data(with_regressions=True, has_critical=True), out)
    text = out.read_text(encoding="utf-8")
    assert "CRITICAL" in text
    assert "🔴" in text


def test_write_compare_report_warnings(tmp_path):
    out = tmp_path / "compare.md"
    write_compare_report(_compare_data(with_regressions=True, has_warnings=True), out)
    text = out.read_text(encoding="utf-8")
    assert "Warnings" in text or "🟡" in text


def test_write_compare_report_creates_parent_dirs(tmp_path):
    out = tmp_path / "a" / "b" / "compare.md"
    write_compare_report(_compare_data(), out)
    assert out.exists()


# ── write_json ────────────────────────────────────────────────────────────────

def test_write_json_creates_file(tmp_path):
    out = tmp_path / "data.json"
    write_json({"key": "value"}, out)
    assert out.exists()


def test_write_json_roundtrip(tmp_path):
    out = tmp_path / "data.json"
    data = {"score": 0.85, "items": [1, 2, 3]}
    write_json(data, out)
    assert json.loads(out.read_text(encoding="utf-8")) == data


def test_write_json_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "dir" / "out.json"
    write_json({"x": 1}, out)
    assert out.exists()


def test_write_json_pretty_printed(tmp_path):
    out = tmp_path / "out.json"
    write_json({"a": 1}, out)
    text = out.read_text(encoding="utf-8")
    assert "\n" in text  # indented
