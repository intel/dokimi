# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Item analysis: which probes measure anything, and what the module refuses to compute."""

from __future__ import annotations

import json

import pytest

from dokimi.eval.stats import point_biserial
from dokimi.novelty.items import (
    MIN_DISCRIMINATION,
    ProbeFileError,
    discrimination,
    item_analysis,
    kind_agreement,
    load_probe_cells,
)
from dokimi.novelty.measure import BASELINE_ARM, ERRORED, ProbeRun


def _run(atom: str, kind: str, arm: str, correct: bool, errored: bool = False) -> ProbeRun:
    verdict = ERRORED if errored else ("correct_complete" if correct else "wrong_other")
    return ProbeRun(atom_id=atom, probe_kind=kind, arm=arm, question="q", response="r",
                    verdict=verdict)


def test_an_atom_answered_differently_per_wording_is_named():
    runs = [
        _run("a1", "direct", "full", True), _run("a1", "paraphrase", "full", True),
        _run("a2", "direct", "full", True), _run("a2", "paraphrase", "full", False),
        # Reversal disagreeing is its job, not a defect: its key is "refuse".
        _run("a1", "reversal", "full", False),
        # Single-kind atoms carry no agreement information either way.
        _run("a3", "direct", "full", True),
    ]
    agreement = kind_agreement(runs, arm="full")
    assert (agreement["n_atoms_multi_kind"], agreement["n_agree"]) == (2, 1)
    assert agreement["agreement"] == 0.5
    assert [row["atom_id"] for row in agreement["disagreeing"]] == ["a2"]
    assert agreement["disagreeing"][0]["by_kind"] == {"direct": True, "paraphrase": False}


def test_one_probe_kind_per_atom_leaves_agreement_unmeasurable():
    runs = [_run("a1", "direct", "full", True), _run("a2", "direct", "full", False)]
    agreement = kind_agreement(runs, arm="full")
    assert (agreement["agreement"], agreement["n_atoms_multi_kind"]) == (None, 0)


def test_an_errored_run_is_dropped_rather_than_counted_as_disagreement():
    runs = [
        _run("a1", "direct", "full", True),
        _run("a1", "paraphrase", "full", False, errored=True),
    ]
    assert kind_agreement(runs, arm="full")["agreement"] is None


def test_the_baseline_arm_can_be_checked_separately():
    runs = [
        _run("a1", "direct", BASELINE_ARM, True),
        _run("a1", "paraphrase", BASELINE_ARM, False),
        _run("a1", "direct", "full", True), _run("a1", "paraphrase", "full", True),
    ]
    # Prior knowledge that survives only one wording is the case this separates.
    assert kind_agreement(runs, arm=BASELINE_ARM)["agreement"] == 0.0
    assert kind_agreement(runs, arm="full")["agreement"] == 1.0


def test_one_model_cannot_produce_discrimination_and_says_so():
    result = discrimination([{"model": "m1", "runs": [_run("a1", "direct", "full", True)]}])
    assert result["measured"] is False
    assert "at least 3" in result["reason"]
    assert result["per_atom"] == []


def _cell(model: str, outcomes: dict[str, bool]) -> dict:
    return {"model": model,
            "runs": [_run(atom, "direct", "full", ok) for atom, ok in outcomes.items()]}


def test_an_atom_every_model_knows_separates_nothing():
    cells = [
        _cell("strong", {"universal": True, "hard": True, "inverted": False,
                         "f1": True, "f2": True}),
        _cell("middle", {"universal": True, "hard": False, "inverted": False,
                         "f1": True, "f2": True}),
        _cell("weak", {"universal": True, "hard": False, "inverted": True,
                       "f1": False, "f2": False}),
    ]
    result = discrimination(cells)
    assert result["measured"] is True
    rows = {row["atom_id"]: row for row in result["per_atom"]}
    assert rows["universal"]["r"] is None
    assert "no variance in the item" in rows["universal"]["reason"]
    # The hard atom tracks the models' standing; the inverted one runs against it.
    assert rows["hard"]["r"] > MIN_DISCRIMINATION
    assert rows["inverted"]["r"] < 0
    assert result["n_negative"] == 1
    assert result["n_undefined"] == 1                # the universal one
    # Three respondents is below the floor's calibration, so nothing is counted against it.
    assert (result["floor_applies"], result["n_non_discriminating"]) == (False, 0)


def test_an_atom_missing_from_a_cell_is_dropped_not_failed():
    cells = [
        _cell("m1", {"shared": True, "only_here": True, "f1": True}),
        _cell("m2", {"shared": True, "f1": True}),
        _cell("m3", {"shared": False, "f1": False}),
    ]
    result = discrimination(cells)
    rows = {row["atom_id"]: row for row in result["per_atom"]}
    # Counting the two missing cells as failures would have manufactured a correlation,
    # and keeping the atom in one cell's rest score would have moved the shared atoms.
    assert "only_here" not in rows
    assert (result["n_atoms_dropped"], result["atoms_dropped"]) == (1, ["only_here"])
    assert set(rows) == {"shared", "f1"}


def test_an_atom_is_failed_when_any_of_its_kinds_failed():
    cells = [
        {"model": "m1", "runs": [_run("a1", "direct", "full", True),
                                 _run("a1", "paraphrase", "full", False),
                                 _run("a2", "direct", "full", True)]},
        _cell("m2", {"a1": True, "a2": True}), _cell("m3", {"a1": True, "a2": False}),
    ]
    rows = {row["atom_id"]: row for row in discrimination(cells)["per_atom"]}
    assert rows["a1"]["n_pass"] == 2       # the split-kind model counts as a failure


def test_control_arms_do_not_count_as_failing_the_atom():
    # The placebo arm is expected to fail an atom the artifact carries; that must not
    # turn a pass in the artifact arm into a failure.
    cells = [_cell(m, {"a1": True, "a2": ok})
             for m, ok in (("m1", True), ("m2", False), ("m3", True))]
    cells[0]["runs"].append(_run("a1", "direct", "placebo", False))
    cells[1]["runs"].append(_run("a1", "direct", "shuffled", False))
    rows = {row["atom_id"]: row for row in discrimination(cells)["per_atom"]}
    assert rows["a1"]["n_pass"] == 3


def test_point_biserial_lives_in_the_shared_stats_module():
    # It belongs next to the other statistics so a second correlation cannot grow beside
    # it. `items.discrimination` is its only caller today; the point is that the next
    # caller finds it here rather than writing its own.
    assert point_biserial([True, True, False], [1.0, 0.9, 0.2])["r"] > 0.9
    assert point_biserial([True, False], [1.0, 0.2])["r"] is None


def test_an_atom_is_never_correlated_with_a_total_that_contains_it():
    # 3 models, 4 atoms, each model passing exactly one: m1=a1, m2=a2 and a3, m3=a4.
    # An uncorrected item-total correlation calls a2 and a3 perfectly discriminating,
    # because each is most of the total it is being compared with. The rest-score form
    # has no variance left to correlate against, and must say so.
    cells = [
        _cell("m1", {"a1": True, "a2": False, "a3": False, "a4": False}),
        _cell("m2", {"a1": False, "a2": True, "a3": True, "a4": False}),
        _cell("m3", {"a1": False, "a2": False, "a3": False, "a4": True}),
    ]
    result = discrimination(cells)
    rows = {row["atom_id"]: row for row in result["per_atom"]}
    assert rows["a2"]["r"] is None and "no variance in the totals" in rows["a2"]["reason"]
    assert rows["a3"]["r"] is None
    assert result["statistic"] == "corrected_item_total_point_biserial"
    # The note has to say what three respondents can and cannot settle.
    assert "floor is NOT applied" in result["note"]
    assert result["min_discrimination"] is None


def test_the_correlation_still_sees_an_atom_that_tracks_the_rest():
    cells = [
        _cell("strong", {"hard": True, "f1": True, "f2": True, "f3": True}),
        _cell("middle", {"hard": False, "f1": True, "f2": True, "f3": False}),
        _cell("weak", {"hard": False, "f1": False, "f2": False, "f3": False}),
    ]
    rows = {row["atom_id"]: row for row in discrimination(cells)["per_atom"]}
    assert rows["hard"]["r"] > MIN_DISCRIMINATION


def test_repeated_runs_of_one_model_are_one_respondent():
    cells = [_cell("same-model", {"a1": True, "a2": False}) for _ in range(3)]
    result = discrimination(cells)
    assert result["measured"] is False
    assert "distinct model" in result["reason"]


def test_discrimination_counts_distinct_models_among_usable_cells():
    # Three distinct models over four cells, but C has no usable run: two models remain.
    cells = [_cell("A", {"a1": True, "a2": False}), _cell("B", {"a1": False, "a2": True}),
             _cell("C", {}), _cell("A", {"a1": True, "a2": True})]
    result = discrimination(cells)
    assert result["measured"] is False
    assert "2 of 3 models" in result["reason"]


def test_an_arm_nobody_ran_is_not_a_clean_pass():
    cells = [_cell(m, {"a1": True, "a2": False}) for m in ("m1", "m2", "m3")]
    result = discrimination(cells, arm="no_artifact")
    assert result["measured"] is False
    assert "usable runs in arm `no_artifact`" in result["reason"]
    assert (result["n_atoms"], result["n_non_discriminating"]) == (0, 0)


def test_a_cell_with_nothing_else_to_correlate_against_is_not_ranked_weakest():
    # A respondent that scored only this atom has no rest score. Treating that as 0.0
    # ranked it as the weakest model and manufactured a correlation.
    cells = [
        _cell("m1", {"a1": True, "a2": False, "a3": False}),
        _cell("m2", {"a1": False, "a2": True, "a3": True}),
        _cell("m3", {"a1": True}),
    ]
    result = discrimination(cells)
    rows = {row["atom_id"]: row for row in result["per_atom"]}
    assert rows["a1"]["r"] is None
    assert "no rest score" in rows["a1"]["reason"]
    assert result["n_atoms_dropped"] == 2


def test_duplicate_runs_of_one_kind_do_not_depend_on_their_order():
    forward = [_run("a1", "direct", "full", True), _run("a1", "direct", "full", False),
               _run("a1", "paraphrase", "full", True)]
    assert kind_agreement(forward, arm="full")["agreement"] == 0.0
    assert kind_agreement(list(reversed(forward)), arm="full")["agreement"] == 0.0


def _row(arm, n, n_agree):
    return {"arm": arm, "n_atoms_multi_kind": n, "n_agree": n_agree,
            "agreement": n_agree / n, "disagreeing": ["a"] * (n - n_agree)}


def test_a_thin_row_does_not_hide_a_wide_row_that_fails_the_gate():
    from dokimi.gate.novelty_gate import evaluate_novelty
    from test_novelty_gate import _artifact, _named

    wide_fails = {"a.json": _row("full", 40, 20), "b.json": _row("full", 2, 0)}
    check = _named(evaluate_novelty(_artifact(items={"kind_agreement": wide_fails})),
                   "probe_kind_agreement")
    assert (check.passed, check.measured, check.value) == (False, True, 0.5)
    other_arm = {"a.json": _row("full", 40, 38), "b.json": _row("no_artifact", 3, 1)}
    check = _named(evaluate_novelty(_artifact(items={"kind_agreement": other_arm})),
                   "probe_kind_agreement")
    assert check.passed and check.value == 0.95


def test_the_analyze_warning_waits_for_as_many_atoms_as_the_gate():
    from dokimi.novelty.analyze import kind_agreement_warnings

    assert kind_agreement_warnings({"full": _row("full", 1, 0)}) == []
    assert len(kind_agreement_warnings({"full": _row("full", 5, 1)})) == 1


def test_markdown_does_not_print_a_floor_that_was_not_applied():
    from dokimi.novelty.report import _render_items

    text = " ".join(_render_items({"items": {"discrimination": {
        "measured": True, "floor_applies": False, "min_discrimination": None,
        "n_atoms": 9, "n_non_discriminating": 0, "n_negative": 3,
        "models": ["a", "b", "c"]}}}))
    assert "r=None" not in text and "floor not applied to 9 atoms" in text


def _probe_file(tmp_path, name, atoms, **fields):
    runs = [{"atom_id": a, "probe_kind": "direct", "arm": arm, "verdict": "correct_complete"}
            for a in atoms for arm in (BASELINE_ARM, "full")]
    path = tmp_path / name
    path.write_text(json.dumps({"model": name, "runs": runs, **fields}), encoding="utf-8")
    return str(path)


def test_probe_files_load_as_cells_and_warn_on_different_atom_sets(tmp_path):
    a = _probe_file(tmp_path, "a.json", ["x", "y"], artifact="skill:s", ref_digest="d")
    b = _probe_file(tmp_path, "b.json", ["x"])      # older output: no artifact, no digest
    cells, warnings = load_probe_cells([a, b])
    assert [(c["model"], c["source"], len(c["runs"])) for c in cells] == [
        ("a.json", a, 4), ("b.json", b, 2)]
    assert len(warnings) == 1 and "same atom set" in warnings[0]
    record = item_analysis(cells, arm="full")
    assert sorted(record) == ["cells", "discrimination", "kind_agreement"]
    assert sorted(record["kind_agreement"]) == sorted([a, b])    # keyed by file, not model


@pytest.mark.parametrize("payload, message", [
    ({"runs": []}, "carries no probe runs"),
    ({"runs": [{"atom_id": "x"}]}, "missing 'probe_kind'"),
])
def test_a_file_that_is_not_probe_output_is_refused(tmp_path, payload, message):
    path = tmp_path / "p.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProbeFileError, match=message):
        load_probe_cells([str(path)])


def test_runs_of_different_texts_are_not_correlated(tmp_path):
    a = _probe_file(tmp_path, "a.json", ["x"], artifact="skill:s")
    b = _probe_file(tmp_path, "b.json", ["x"], artifact="skill:t")
    with pytest.raises(ProbeFileError, match="measured different texts"):
        load_probe_cells([a, b])
