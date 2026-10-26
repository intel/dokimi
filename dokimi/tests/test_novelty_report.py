# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Markdown novelty report agrees with the gate on whether 0 conflicting atoms was checked."""

import pytest

from dokimi.novelty.report import render_novelty_markdown


def _artifact(**overrides):
    artifact = {
        "artifact": {"ref": "skill:skills/accel-tuning", "kind": "skill", "tokens": 900},
        "model": "m",
        "provider": "openai",
        "judge_model": "j",
        "atoms": {"n": 60, "n_probed": 60, "sampling": "all"},
        "paired": {"lenient": {"added": 0.4, "redundant": 0.5, "degraded": 0.0,
                               "still_missing": 0.1, "n_paired": 60}},
        "k0": {"lenient": 0.5},
        "categories": {"harmful_conflicting": 0, "novel_useful": 30},
        "conflicts": [],
        "conflict_coverage": {"pairs_compared": 1770, "pairs_total": 1770},
        "spans": {"capped_atom_set": False},
    }
    artifact.update(overrides)
    return artifact


@pytest.mark.parametrize("artifact", [
    _artifact(),
    # written before conflict_coverage existed, over an uncapped atom set: the old reading
    {k: v for k, v in _artifact().items() if k != "conflict_coverage"},
], ids=["fully-checked", "no-coverage-record"])
def test_a_zero_the_gate_clears_reads_as_zero(artifact):
    markdown = render_novelty_markdown(artifact)
    assert "| harmful_conflicting | 0 |" in markdown
    assert "Conflicts not cleared" not in markdown and "Conflicts first" not in markdown


@pytest.mark.parametrize("overrides, reason", [
    ({"conflict_coverage": {"pairs_compared": 970, "pairs_total": 1770}},
     "The conflict check compared 970 of 1770 atom pairs"),
    ({"spans": {"capped_atom_set": True, "n_atoms_indexed": 300}}, "The atom set was capped (300 indexed)"),
    ({"conflict_coverage": {"pairs_total": 1770}}, "The conflict_coverage record is malformed"),
])
@pytest.mark.outcome("cjm-novelty-uncleared-conflicts")
def test_a_zero_the_gate_does_not_clear_is_not_shown_as_zero(overrides, reason):
    markdown = render_novelty_markdown(_artifact(**overrides))
    assert f"> **Conflicts not cleared.** {reason}" in markdown
    assert "| harmful_conflicting | not measured (0 found) |" in markdown
    assert "| harmful_conflicting | 0 |" not in markdown


def test_found_conflicts_lead_whatever_the_coverage():
    markdown = render_novelty_markdown(_artifact(
        categories={"harmful_conflicting": 2, "novel_useful": 30},
        conflicts=[{"a": 1, "b": 2}], conflict_coverage={"pairs_compared": 970, "pairs_total": 1770}))
    assert "> **Conflicts first: 2 conflicting atoms, 1 pairs.**" in markdown
    assert "Conflicts not cleared" not in markdown
    assert "| harmful_conflicting | 2 |" in markdown
