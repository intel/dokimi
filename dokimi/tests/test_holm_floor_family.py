# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A result that cannot reach p < 0.05 stays out of Holm's and BH's multiplier (issue #478).

Its sign-flip p cannot go below ``2 / 2**k`` over its k changed pairs, so it can never be significant and
adding it to the family only costs the others power. It keeps its row, its p and its null verdict, still
counts in ``family_size``, and has no adjusted p; ``family_tested`` counts the results that can reach 0.05.
"""

from __future__ import annotations

import pytest

from dokimi.eval import stats
from dokimi.report.model_compare import generate_section_report
from dokimi.repos.compare import compare, format_compare
from dokimi.repos.compare_reach import holm_floor

from test_repo_compare import _row
from test_repo_compare_reading import SIX_MIXED, _two_score_arms

SIX = [10, 12, 9, 11, 8, 13]  # six changed pairs of one sign: p 2/64
TWO = [5, -3]  # two changed pairs: p 1.0, the floor is 0.5
EIGHT = [5, 6, 7, 8, 9, 10, 11, -1]  # p 6/256


def _corrected(delta_lists: list[list[float]], correction: str = "holm") -> list[dict]:
    results = [stats._describe_deltas([float(x) for x in d], confidence=0.95, seed=0) for d in delta_lists]
    stats._apply_correction(results, correction=correction, label="arms")
    return [stats._finalize(r) for r in results]


def test_the_issue_repro_a_two_pair_run_no_longer_costs_the_six_pair_run_its_verdict():
    alone = _corrected([SIX])[0]
    together, small = _corrected([SIX, TWO])
    assert (alone["p"], alone["p_adjusted"], alone["significant_adjusted"]) == (0.0312, 0.0312, True)
    assert (together["p_adjusted"], together["significant_adjusted"]) == (0.0312, True)
    assert (together["family_size"], together["family_tested"]) == (2, 1)
    assert (alone["family_size"], alone["family_tested"]) == (1, 1)
    # the floor-bound run keeps its p and null verdict, and shows no adjusted p
    assert (small["p"], small["n_changed"], small["significant"]) == (1.0, 2, None)
    assert (small["p_adjusted"], small["exact"]["p_adjusted"], small["significant_adjusted"]) == (None, None, None)
    assert (small["family_size"], small["family_tested"]) == (2, 1)


@pytest.mark.parametrize("correction", ["holm", "bh"])
def test_a_floor_bound_member_leaves_every_other_adjusted_p_unchanged(correction):
    without = _corrected([SIX, EIGHT], correction)
    with_bound = _corrected([SIX, TWO, EIGHT, [1, 2, 3]], correction)
    kept = [with_bound[0], with_bound[2]]
    assert [r["p_adjusted"] for r in kept] == [r["p_adjusted"] for r in without]
    assert [r["exact"]["p_adjusted"] for r in kept] == [r["exact"]["p_adjusted"] for r in without]
    assert [r["significant_adjusted"] for r in kept] == [r["significant_adjusted"] for r in without]
    assert [(r["family_size"], r["family_tested"]) for r in with_bound] == [(4, 2)] * 4
    assert [r["p_adjusted"] for r in (with_bound[1], with_bound[3])] == [None, None]


def test_a_family_of_only_floor_bound_results_has_no_adjusted_p_and_no_verdict():
    results = _corrected([TWO, [1, 2, 3, 4], [1, 2, 3, 4, 5]])
    assert [r["p_adjusted"] for r in results] == [None] * 3
    assert [r["significant_adjusted"] for r in results] == [None] * 3
    assert [(r["family_size"], r["family_tested"]) for r in results] == [(3, 0)] * 3


def test_a_result_with_no_p_is_out_of_the_family_as_before():
    tied, six = _corrected([[0, 0, 0], SIX])
    assert tied["p"] is None and tied["p_adjusted"] is None and (six["family_size"], six["family_tested"]) == (2, 1)


# ---------------------------------------------------------------------------
# report model-compare
# ---------------------------------------------------------------------------


def _run(deltas: list[float]) -> dict:
    return {"arms": ["baseline", "docs"], "baseline_arm": "baseline", "evaluations": [
        {"question_id": f"q{i}", "question_text": f"Q{i}",
         "scores": {"baseline": {"aggregate": 50.0}, "docs": {"aggregate": 50.0 + d}}}
        for i, d in enumerate(deltas)]}


def test_model_compare_keeps_a_run_of_two_pairs_out_of_the_multiplier():
    text = "\n".join(generate_section_report([("six", _run([float(d) for d in SIX])), ("two", _run([5.0, -3.0, 0.0, 0.0, 0.0, 0.0]))],
                                             ["six", "two"], "Regular"))
    section = text.split("## Statistical Significance")[1].split("\n## ")[0]
    assert "adjusted across the 1 run(s)" in section
    assert "| **six** | +10.5 | 0.0312 | 0.0312 | 6 of 6 |" in section and "| OK Yes |" in section
    assert "| **two** | +0.3 | 1.0 | N/A | 2 of 6 |" in section and section.rstrip().endswith("N/A |")


# ---------------------------------------------------------------------------
# repo compare
# ---------------------------------------------------------------------------

FOUR_MIXED = [0.5, -0.5, 0.25, -0.25, 0.0, 0.0]  # four changed tasks: p floor 0.125


def test_holm_floor_leaves_a_floor_bound_row_out_of_the_family():
    assert holm_floor([0.03, 0.25], [8, 3], 0) == (0.0078, True)  # 2/256 alone; a family of two gave 0.0156
    assert holm_floor([0.03, 0.25], [8, 3], 1) == (None, None)  # 2/8 is not below 0.05: no floor to read
    assert holm_floor([0.03, 0.04], [8, 8], 0) == (0.0156, True)  # a testable member still counts


def test_a_floor_bound_row_is_out_of_p_holm_and_does_not_move_the_floor_or_mde_of_another(tmp_path):
    _two_score_arms(tmp_path / "pair", SIX_MIXED, FOUR_MIXED)
    _two_score_arms(tmp_path / "alone", SIX_MIXED, SIX_MIXED)
    with_bound = compare([tmp_path / "pair"], "guidance:none", ["guidance:raw"])
    raw_alone = _row(with_bound, "score")
    both = compare([tmp_path / "pair"], "guidance:none")
    raw, other = _row(both, "score"), _row(both, "score", treatment="guidance:other")
    assert raw["p_holm"] == raw_alone["p_holm"] == pytest.approx(raw["p"], abs=1e-4)  # a family of one
    assert raw["p_holm_floor"] == pytest.approx(2 / 64, abs=1e-4) and raw["mde"] is not None
    assert raw["mde"] == raw_alone["mde"]
    assert other["p"] is not None and other["p_holm"] is None and other["p_holm_floor"] is None
    assert other["mde"] is None and other["interval_p_disagree"] is False
    text = format_compare(both)
    assert "with 4 changed tasks the sign-flip p cannot go below 0.125: no effect can reach p < 0.05 here" in text
    assert "Holm over" not in text


def test_a_family_of_only_floor_bound_rows_has_no_p_holm(tmp_path):
    _two_score_arms(tmp_path / "b", FOUR_MIXED, FOUR_MIXED)
    s = compare([tmp_path / "b"], "guidance:none")
    for arm in ("guidance:raw", "guidance:other"):
        row = _row(s, "score", treatment=arm)
        assert row["p"] is not None and row["p_holm"] is None and row["p_holm_floor"] is None and row["mde"] is None
    assert "Holm over" not in format_compare(s)
