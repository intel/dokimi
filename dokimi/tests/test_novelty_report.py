# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Markdown novelty report agrees with the gate on whether 0 conflicting atoms was checked."""

import pytest

from dokimi.novelty.report import _capped_at, coverage_reach, render_novelty_markdown


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


@pytest.mark.parametrize("cost, line", [
    ({"llm_calls": 2, "input_tokens": 200, "output_tokens": 20, "usd": 0.02},
     "Cost: 2 LLM calls, 200 in / 20 out, usd 0.02."),
    ({"llm_calls": 3, "input_tokens": 10, "output_tokens": 1, "usd": 0.001, "replayed_calls": 1},
     "Cost: 3 LLM calls, 10 in / 1 out, usd 0.001, 1 replayed from the cache and not counted."),
], ids=["live", "replayed"])
def test_the_cost_line_names_replayed_calls_only_when_there_are_some(cost, line):
    assert line in render_novelty_markdown(_artifact(cost=cost)).splitlines()


def test_a_cap_that_stopped_on_the_last_span_does_not_say_the_atoms_come_from_the_opening():
    end = {"last_span": "s0010", "spans": 10, "of": 10, "tokens_share": 1.0}
    assert coverage_reach({"reached": end}) == (
        "went into the input", "; the capped atoms reach its last span, and the cap dropped any atoms past it")
    assert "opening" not in _capped_at({"extraction": {"reached": end}})
    from dokimi.novelty.report import cap_warning

    assert cap_warning(30, end) == (
        "atom extraction hit the cap of 30 at span s0010 (10 of 10 spans, 100% of the tokens): any atoms past "
        "the cap in its last reply were dropped; pass --all-atoms for the full sweep")


def test_a_cap_that_stopped_inside_the_artifact_still_says_the_rates_describe_the_opening():
    inside = {"last_span": "s0002", "spans": 2, "of": 5, "tokens_share": 0.3571}
    assert coverage_reach({"reached": inside}) == (
        "went into the input", "; the capped atoms come from the first 35% of its tokens")
    from dokimi.novelty.report import cap_warning

    assert cap_warning(1, inside) == (
        "atom extraction hit the cap of 1 at span s0002 (2 of 5 spans, 35% of the tokens): rates describe "
        "the opening of the artifact, not the whole; pass --all-atoms for the full sweep")
