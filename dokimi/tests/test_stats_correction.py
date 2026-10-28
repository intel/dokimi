# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Significance after a multiplicity correction is decided on the unrounded p.

The adjusted p is displayed rounded to 4 decimals, but the verdict against
ALPHA uses the exact value. Three tests at p = 0.0166662 have an exact Holm p
of 0.0499986 (significant) that displays as 0.05, which the rounded value
fails to beat. Each boundary case below runs through the site that decides
the verdict. The helpers are reached as attributes inside each test, so this
file imports on code that predates ``correct_family``.
"""

from __future__ import annotations

from dokimi.eval import stats
from dokimi.eval.cross_judge import apply_holm
from dokimi.report import model_compare

# Exact Holm p = 0.0499986 (significant), displayed as 0.05.
BOUNDARY_IN = 0.0166662
# Exact Holm p = 0.0500004 (not significant), displayed as 0.05 too.
BOUNDARY_OUT = 0.0166668


# ---------------------------------------------------------------------------
# correct_family: the shared (display, decision) helper
# ---------------------------------------------------------------------------


def test_correct_family_decides_on_the_unrounded_adjusted_p():
    out = stats.correct_family([BOUNDARY_IN] * 3, method="holm")
    assert out == [(0.05, True)] * 3


def test_correct_family_just_above_alpha_is_not_significant():
    out = stats.correct_family([BOUNDARY_OUT] * 3, method="holm")
    assert out == [(0.05, False)] * 3


def test_correct_family_untested_entries_are_none_and_not_in_the_family():
    out = stats.correct_family([None, BOUNDARY_IN, None], method="holm")
    # The family is one test, so its Holm p is the raw p itself.
    assert out == [(None, None), (0.0167, True), (None, None)]


def test_correct_family_display_matches_adjust_pvalues():
    raw = [0.001, 0.02, 0.03, None, 0.9]
    for method in stats.CORRECTIONS:
        shown = [p for p, _ in stats.correct_family(raw, method=method)]
        assert shown == stats.adjust_pvalues(raw, method=method)


# ---------------------------------------------------------------------------
# _apply_correction: the arms and interactions families
# ---------------------------------------------------------------------------


def test_apply_correction_boundary_is_significant_for_the_arms_family():
    results = [{"p_ttest": BOUNDARY_IN, "p_wilcoxon": None} for _ in range(3)]
    stats._apply_correction(results, correction="holm", label="arms")
    for res in results:
        assert res["p_adjusted"] == 0.05  # display stays rounded
        assert res["significant_adjusted"] is True


def test_apply_correction_boundary_just_above_alpha_is_not_significant():
    results = [{"p_ttest": BOUNDARY_OUT, "p_wilcoxon": None} for _ in range(3)]
    stats._apply_correction(results, correction="holm", label="arms")
    assert [r["significant_adjusted"] for r in results] == [False, False, False]


def test_apply_correction_wilcoxon_still_gates_the_verdict():
    results = [{"p_ttest": BOUNDARY_IN, "p_wilcoxon": 0.2} for _ in range(3)]
    stats._apply_correction(results, correction="holm", label="arms")
    assert [r["significant_adjusted"] for r in results] == [False, False, False]


def test_apply_correction_records_family_fields_and_none_entries():
    results = [
        {"p_ttest": 0.01, "p_wilcoxon": 0.02},
        {"p_ttest": None, "p_wilcoxon": None},
        {"p_ttest": 0.04, "p_wilcoxon": 0.5},
    ]
    stats._apply_correction(results, correction="holm", label="interactions")
    # Holm over the two testable p-values: 2*0.01 = 0.02, then max(0.02, 1*0.04).
    assert [r["p_adjusted"] for r in results] == [0.02, None, 0.04]
    assert [r["significant_adjusted"] for r in results] == [True, None, False]
    for res in results:
        assert res["correction"] == "holm"
        assert res["correction_family"] == "interactions"
        assert res["family_size"] == 3
        assert res["family_tested"] == 2


def test_apply_correction_uses_the_requested_method():
    results = [{"p_ttest": 0.04, "p_wilcoxon": None}, {"p_ttest": 0.01, "p_wilcoxon": None}]
    stats._apply_correction(results, correction="bh", label="arms")
    # BH on [0.04, 0.01]: step-up gives 0.04 and min(0.04, 0.01 * 2) = 0.02.
    assert [r["p_adjusted"] for r in results] == [0.04, 0.02]
    assert all(r["correction"] == "bh" for r in results)


def test_apply_correction_on_an_empty_family_adds_nothing():
    results: list[dict] = []
    stats._apply_correction(results, correction="holm", label="arms")
    assert results == []


# ---------------------------------------------------------------------------
# _adjusted_significance: the per-run family of the model comparison
# ---------------------------------------------------------------------------


def _three_runs(monkeypatch, p_ttest, p_wilcoxon=None):
    fake = {"p_ttest": p_ttest, "p_wilcoxon": p_wilcoxon, "significant": True}
    monkeypatch.setattr(model_compare, "significance_test", lambda *_, **__: dict(fake))
    run_ids = ["a", "b", "c"]
    all_run_scores = {rid: [{"with_docs": 60, "without_docs": 50}] * 6 for rid in run_ids}
    return model_compare._adjusted_significance(run_ids, all_run_scores, "holm")


def test_model_compare_boundary_is_significant(monkeypatch):
    sigs, tested = _three_runs(monkeypatch, BOUNDARY_IN)
    assert tested == 3
    for sig in sigs.values():
        assert sig["p_adjusted"] == 0.05  # display stays rounded
        assert sig["significant_adjusted"] is True


def test_model_compare_boundary_just_above_alpha_is_not_significant(monkeypatch):
    sigs, tested = _three_runs(monkeypatch, BOUNDARY_OUT)
    assert tested == 3
    assert all(sig["significant_adjusted"] is False for sig in sigs.values())


# Real scores whose per-run t-test p is 0.0166623, displayed 0.0167.
SHIFTS = [-10, -5, 0, 5, 10, 15, 20, -15, 25, 30]


def test_paired_test_rounds_by_default_and_on_request_leaves_p_exact():
    treatment, control = [56.543 + s for s in SHIFTS], [50.0] * len(SHIFTS)
    assert stats.paired_test(treatment, control)["p_ttest"] == 0.0167
    exact = stats.paired_test(treatment, control, rounded=False)["p_ttest"]
    assert 0.01665 < exact < 0.05 / 3


def test_model_compare_corrects_real_scores_before_rounding():
    # Unmocked: Holm on the exact p is 0.049987 (significant); on the displayed
    # 0.0167 it would be 0.0501.
    rows = [{"with_docs": 56.543 + s, "without_docs": 50.0} for s in SHIFTS]
    run_ids = ["a", "b", "c"]
    sigs, tested = model_compare._adjusted_significance(
        run_ids, {rid: rows for rid in run_ids}, "holm"
    )
    assert tested == 3
    for sig in sigs.values():
        assert (sig["p_ttest"], sig["p_adjusted"]) == (0.0167, 0.05)  # displayed rounded
        assert sig["significant_adjusted"] is True


def test_model_compare_skips_runs_without_pairs(monkeypatch):
    fake = {"p_ttest": BOUNDARY_IN, "p_wilcoxon": None, "significant": True}
    monkeypatch.setattr(model_compare, "significance_test", lambda *_, **__: dict(fake))
    all_run_scores = {
        "a": [{"with_docs": 60, "without_docs": 50}] * 6,
        "b": [{"with_docs": None, "without_docs": 50}] * 6,
    }
    sigs, tested = model_compare._adjusted_significance(["a", "b"], all_run_scores, "holm")
    assert tested == 1
    assert sigs["b"] is None
    assert sigs["a"]["p_adjusted"] == 0.0167  # a family of one: adjusted = raw, displayed rounded
    assert sigs["a"]["significant_adjusted"] is True


# ---------------------------------------------------------------------------
# apply_holm (cross-judge): the reference the other sites now match
# ---------------------------------------------------------------------------


def _row(gate, p, name="m"):
    return {"measure": name, "gate": gate, "p_sign": p}


def test_apply_holm_boundary_is_significant_and_displays_rounded():
    rows = [_row("PASS", BOUNDARY_IN, f"m{i}") for i in range(3)]
    apply_holm(rows)
    assert all(r["significant"] is True for r in rows)
    assert all(r["p_holm"] == 0.05 for r in rows)


def test_apply_holm_just_above_alpha_is_not_significant():
    rows = [_row("PASS", BOUNDARY_OUT, f"m{i}") for i in range(3)]
    apply_holm(rows)
    assert all(r["significant"] is False for r in rows)


def test_apply_holm_excludes_failed_judges_from_the_family():
    rows = [_row("FAIL", 0.0001, "bad"), _row("PASS", 0.02, "a"), _row("PASS", 0.6, "b")]
    apply_holm(rows)
    bad, a, b = rows
    assert bad["votes"] is False and bad["p_holm"] is None and bad["significant"] is False
    # Family of two voters: 2 * 0.02 = 0.04 for the first, and the second stays at 0.6.
    assert a["votes"] is True and a["p_holm"] == 0.04 and a["significant"] is True
    assert b["votes"] is True and b["p_holm"] == 0.6 and b["significant"] is False


def test_apply_holm_require_validated_keeps_only_passing_judges():
    rows = [_row("PASS", 0.01, "a"), _row(None, 0.01, "unknown"), _row("FAIL", 0.01, "c")]
    apply_holm(rows, require_validated=True)
    assert [r["votes"] for r in rows] == [True, False, False]


def test_apply_holm_untestable_voter_is_not_significant():
    rows = [_row("PASS", None, "none"), _row("PASS", 0.01, "a")]
    apply_holm(rows)
    none_row, a = rows
    assert none_row["p_holm"] is None and none_row["significant"] is False
    assert a["significant"] is True
