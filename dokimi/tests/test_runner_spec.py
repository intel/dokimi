# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for runtime spec validation and golden_manifest enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.runner.spec import (
    SpecValidationError,
    is_full_spec,
    load_spec,
    select_docs,
    validate_spec,
)

REAL_SPEC = Path(__file__).resolve().parents[1] / "benchmarks" / "spec.v1.yaml"


def test_real_spec_validates():
    """The shipped spec must satisfy its own schema (catches schema/spec drift)."""
    spec = load_spec(REAL_SPEC)
    validate_spec(spec)  # must not raise
    assert is_full_spec(spec)


def test_full_spec_invalid_enum_raises():
    spec = load_spec(REAL_SPEC)
    spec["mode"] = "not_a_real_mode"
    with pytest.raises(SpecValidationError, match="validation failed"):
        validate_spec(spec)


def test_full_spec_extra_property_raises():
    spec = load_spec(REAL_SPEC)
    spec["surprise"] = True
    with pytest.raises(SpecValidationError):
        validate_spec(spec)


def test_load_spec_validates_full_spec(tmp_path):
    """A spec that declares version:1 but is broken must fail on load."""
    bad = tmp_path / "spec.yaml"
    bad.write_text("version: 1\nname: x\n")  # missing required v1 fields
    with pytest.raises(SpecValidationError):
        load_spec(bad)


def test_load_spec_skips_minimal_spec(tmp_path):
    """Minimal/legacy specs (no version/manifest) are not schema-validated."""
    minimal = tmp_path / "spec.yaml"
    minimal.write_text("weights:\n  coverage: 1.0\nmetrics: {}\n")
    spec = load_spec(minimal)
    assert not is_full_spec(spec)


def _make_docs(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a")
    (root / "docs" / "b.md").write_text("b")
    (root / "docs" / "archive").mkdir()
    (root / "docs" / "archive" / "old.md").write_text("old")


def test_select_docs_include_exclude(tmp_path):
    _make_docs(tmp_path)
    manifest = {
        "include": ["docs/**/*.md"],
        "exclude": ["docs/archive/**"],
        "min_docs": 1,
        "max_docs": 10,
    }
    docs = select_docs(tmp_path, manifest)
    names = sorted(p.name for p in docs)
    assert names == ["a.md", "b.md"]  # archive/old.md excluded


def test_select_docs_min_docs_violation(tmp_path):
    _make_docs(tmp_path)
    manifest = {"include": ["docs/**/*.md"], "exclude": ["docs/archive/**"], "min_docs": 5, "max_docs": 10}
    with pytest.raises(ValueError, match="min_docs"):
        select_docs(tmp_path, manifest)


def test_select_docs_max_docs_violation(tmp_path):
    _make_docs(tmp_path)
    manifest = {"include": ["docs/**/*.md"], "exclude": [], "min_docs": 1, "max_docs": 2}
    with pytest.raises(ValueError, match="max_docs"):
        select_docs(tmp_path, manifest)


def test_run_benchmark_uses_manifest(tmp_path):
    """run_benchmark must select docs from the manifest, not a fixed docs/ glob."""
    from dokimi.runner.run import run_benchmark

    _make_docs(tmp_path)
    spec = tmp_path / "spec.yaml"
    # Minimal-but-manifested spec: has golden_manifest so manifest path is taken,
    # but no version so schema validation is skipped (keeps the test self-contained).
    spec.write_text(
        "weights:\n  coverage: 0.5\n  freshness_lite: 0.25\n  readability: 0.25\n"
        "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n"
        "golden_manifest:\n"
        "  include: ['docs/**/*.md']\n"
        "  exclude: ['docs/archive/**']\n"
        "  min_docs: 1\n  max_docs: 10\n"
    )
    out = run_benchmark(tmp_path, spec)
    # a.md + b.md selected, archive/old.md excluded.
    assert out["summary"]["docs"] == 2


def test_null_example_memory_cap_validates_and_preserves_host_opt_in():
    from dokimi.metrics.example_runner import ExecutionPolicy, run_example
    from dokimi.process import FakeRunner

    spec = load_spec(REAL_SPEC)
    metric = spec["metrics"]["example_pass_rate"]
    metric["execution"] = {"backend": "subprocess", "mem_mb": None}
    validate_spec(spec)
    runner = FakeRunner()
    result = run_example("python", "print(1)", ExecutionPolicy.from_config(metric), runner)
    assert result.status == "failed" and "allow_host" in (result.error or "")
    assert runner.calls == []
    metric["execution"]["mem_mb"] = "unlimited"
    with pytest.raises(SpecValidationError, match="mem_mb"):
        validate_spec(spec)
