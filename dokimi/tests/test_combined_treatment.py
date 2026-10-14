# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for combined treatment feature."""

from dokimi.treatments.factory import create_treatment
from dokimi.treatments.arms import CombinedTreatment


def test_create_combined_treatment():
    """Test creating combined treatment from spec."""
    treatment = create_treatment("baseline+docs")
    assert isinstance(treatment, CombinedTreatment)
    assert len(treatment.treatments) == 2
    assert "+" in treatment.name


def test_combined_treatment_backward_compat():
    """Test that individual specs still work."""
    baseline = create_treatment("baseline")
    assert baseline.name == "baseline"
    assert not isinstance(baseline, CombinedTreatment)

    docs = create_treatment("docs")
    assert docs.name == "docs"
    assert not isinstance(docs, CombinedTreatment)


def test_combined_treatment_name():
    """Test that combined treatment has correct name."""
    treatment = create_treatment("baseline+docs")
    assert treatment.name == "baseline+docs"


def test_combined_treatment_prepare(tmp_path):
    """Test that combined treatment merges contexts."""
    # Create a simple skill file
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("""---
name: test-skill
description: Test skill for unit tests
---
# Test Skill
This is a test skill.""")

    # Create combined treatment
    treatment = create_treatment(f"baseline+skill:{skill_dir}")
    assert isinstance(treatment, CombinedTreatment)

    # Prepare config
    config = treatment.prepare(
        question_text="How do I test?",
        library_name="test-lib"
    )

    # Check metadata
    assert config.metadata["arm_kind"] == "combined"
    assert config.metadata["combined_count"] == 2
    assert len(config.metadata["treatments"]) == 2

    # Baseline contributes no context, skill contributes one chunk
    assert len(config.injected_context) == 1


def test_mcp_urls_with_plus_not_combined():
    """Test that MCP URLs with + are not treated as combined."""
    # MCP URLs might have + in query params - should NOT be parsed as combined
    spec = "mcp:http=https://example.com/mcp?foo=bar+baz"
    treatment = create_treatment(spec)
    # Should NOT be CombinedTreatment since it starts with mcp:
    assert not isinstance(treatment, CombinedTreatment)
