# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The novelty gate's soft checks, and the scorecard block built from them."""

from __future__ import annotations

import pytest

from dokimi.gate.novelty_gate import (
    evaluate_novelty,
    format_novelty_block,
    uncleared_conflicts,
)
from dokimi.subjects.novelty_track import (
    build_novelty_block,
    load_novelty_artifacts,
    novelty_warnings,
)


def _artifact(**overrides):
    """A healthy novelty artifact: every check passes, nothing is skipped."""
    artifact = {
        "artifact": {"ref": "skill:skills/accel-tuning", "kind": "skill"},
        "model": "m",
        "provider": "openai",
        "judge_model": "j",
        "atoms": {"n": 40, "n_probed": 40, "sampling": "all"},
        "paired": {
            "lenient": {"added": 0.42, "redundant": 0.5, "degraded": 0.0,
                        "still_missing": 0.08, "n_paired": 40, "ci95_added": [0.3, 0.55]},
            "strict": {"added": 0.30, "redundant": 0.6, "degraded": 0.0,
                       "still_missing": 0.10, "n_paired": 40, "ci95_added": [0.2, 0.4]},
        },
        "k0": {"lenient": 0.5, "strict": 0.6},
        "categories": {"harmful_conflicting": 0, "novel_useful": 30},
        "spans": {"n": 60, "tokens": 900, "frac_carrying_nothing": 0.12,
                  "capped_atom_set": False},
        "shortening": [{"safe": True, "protection": 1.0, "value_coverage": 0.9,
                        "value_is_coverage_proxy": False}],
        "attribution": {"carrier_confirmation": {"n_atoms": 40, "n_located": 38,
                                                 "n_unlocated": 2, "frac_located": 0.95}},
        "controls": {"order_independent": False},
        "arms": {"lenient": {"no_artifact": 0.4, "full": 0.8}},
        "items": {"kind_agreement": {
            "no_artifact": {"arm": "no_artifact", "n_atoms_multi_kind": 40, "n_agree": 36,
                            "agreement": 0.9, "disagreeing": []},
            "full": {"arm": "full", "n_atoms_multi_kind": 40, "n_agree": 38,
                     "agreement": 0.95, "disagreeing": []},
        }},
        "bundle": {"include_resources": True, "n_files": 3, "entry_point_words": 400,
                   "resource_words_measured": 900, "resource_words_on_disk": 900,
                   "outside_share": 0.69, "excluded": [],
                   "pointer_spans": {"n": 2, "tokens": 18}},
        "probes": {"n_probes": 110, "malformed_rate": 0.02,
                   "by_kind": {"paraphrase": {"generated": 40, "kept": 39, "rejected": 1,
                                              "malformed_rate": 0.025, "reasons":
                                              {"anchor_dropped": 1}}}},
        "warnings": [],
    }
    artifact.update(overrides)
    return artifact


def _named(verdict, name):
    return next(check for check in verdict.checks if check.name == name)


def test_a_healthy_artifact_passes_every_check():
    verdict = evaluate_novelty(_artifact())
    assert verdict.status == "PASS"
    assert verdict.blocking is False
    assert [check.name for check in verdict.checks if not check.passed] == []
    assert verdict.not_measured == []


def test_a_run_that_skipped_its_stages_says_so_instead_of_passing():
    # --no-classify --no-shorten --no-confirm-carriers, and no shuffled arm (the default arms).
    verdict = evaluate_novelty(_artifact(
        categories={}, shortening=[], attribution={}, controls={}, spans={"n": 60},
        bundle={}, probes={}, items={}))
    assert verdict.not_measured == [
        "harmful_atoms", "dead_tokens", "artifact_coverage", "probe_wording_fidelity",
        "probe_kind_agreement", "protection_invariant", "span_citations_confirmed",
        "value_is_measured", "order_independence",
    ]
    block = format_novelty_block([verdict])
    assert "(9 of 10 not measured)" in block
    assert "| harmful_atoms | not measured | None |" in block


def test_unclassified_run_does_not_report_zero_harmful():
    # The `standard` tier skips classification; the row once read "0 atoms classified
    # harmful/conflicting" next to a PASS.
    check = _named(evaluate_novelty(_artifact(categories={})), "harmful_atoms")
    assert check.measured is False and check.value is None
    assert "not classified" in check.message
    assert not check.message.startswith("0 atoms")
    classified = _named(evaluate_novelty(_artifact()), "harmful_atoms")
    assert classified.message.startswith("0 atoms classified")


def test_a_partial_conflict_check_does_not_clear_harmful_atoms():
    # A batched classify that compared 20 of 45 pairs and found nothing used to set
    # measured=True, passed=True, value=0. That is a cleared check of a scan that
    # did not happen. A count of 1 still warns; a full pair space may still pass.
    partial = evaluate_novelty(_artifact(
        conflict_coverage={"pairs_compared": 20, "pairs_total": 45}))
    check = _named(partial, "harmful_atoms")
    assert check.measured is False and check.value is None
    assert "harmful_atoms" in partial.not_measured
    assert partial.status == "PASS"
    assert "| harmful_atoms | not measured | None |" in format_novelty_block([partial])
    assert not check.message.startswith("0 atoms")

    full = _named(evaluate_novelty(_artifact(
        conflict_coverage={"pairs_compared": 45, "pairs_total": 45})), "harmful_atoms")
    assert full.measured is True and full.passed is True
    assert full.message.startswith("0 atoms classified")

    found = evaluate_novelty(_artifact(
        categories={"harmful_conflicting": 1},
        conflict_coverage={"pairs_compared": 20, "pairs_total": 45}))
    found_check = _named(found, "harmful_atoms")
    assert found_check.measured is True and found_check.passed is False
    assert found.status == "WARN"


@pytest.mark.parametrize("name, overrides", [
    ("added_floor", {"paired": {"lenient": {"added": 0.04, "n_paired": 40}}}),
    ("harmful_atoms", {"categories": {"harmful_conflicting": 3}}),
    ("dead_tokens", {"spans": {"frac_carrying_nothing": 0.74, "capped_atom_set": False}}),
    ("protection_invariant", {"shortening": [{"safe": True, "protection": 0.158}]}),
    ("span_citations_confirmed", {"attribution": {"carrier_confirmation": {
        "n_atoms": 40, "n_located": 20, "n_unlocated": 20, "frac_located": 0.5}}}),
    ("value_is_measured", {"shortening": [{"safe": True, "protection": 1.0,
                                           "value_coverage": 0.2,
                                           "value_is_coverage_proxy": True}]}),
])
def test_each_failing_check_warns_and_never_blocks(name, overrides):
    verdict = evaluate_novelty(_artifact(**overrides))
    check = _named(verdict, name)
    assert check.passed is False
    assert check.severity == "warn"
    assert verdict.status == "WARN"
    # The whole point of the gate: a novelty number is a profile, so nothing here stops a build.
    assert verdict.blocking is False


def test_a_capped_atom_set_skips_the_dead_token_check_instead_of_passing_it():
    verdict = evaluate_novelty(_artifact(
        spans={"frac_carrying_nothing": 0.743, "capped_atom_set": True, "n_atoms_indexed": 40}
    ))
    check = _named(verdict, "dead_tokens")
    assert (check.passed, check.severity) == (True, "info")
    assert "capped" in check.message
    assert verdict.status == "PASS"


def test_an_unconfirmed_run_is_reported_as_unchecked_not_as_confirmed():
    artifact = _artifact()
    artifact["attribution"] = {}
    check = _named(evaluate_novelty(artifact), "span_citations_confirmed")
    assert (check.passed, check.severity, check.value) == (True, "info", None)
    assert "not confirmed" in check.message


@pytest.mark.parametrize("paired", [
    {},
    {"lenient": {"added": 0.0, "redundant": 0.0, "degraded": 0.0, "still_missing": 0.0,
                 "n_paired": 0}},
])
def test_a_run_without_paired_probes_reports_added_as_unmeasured(paired):
    verdict = evaluate_novelty(_artifact(paired=paired))
    check = _named(verdict, "added_floor")
    assert (check.passed, check.severity, check.value) == (True, "info", None)
    assert "added_floor" in verdict.not_measured


def test_order_independence_is_recorded_as_a_note_not_a_defect():
    verdict = evaluate_novelty(_artifact(controls={"order_independent": True}))
    check = _named(verdict, "order_independence")
    assert (check.passed, check.severity) == (False, "info")
    assert verdict.status == "PASS"
    assert [c.name for c in verdict.notes] == ["order_independence"]


def test_thresholds_are_overridable_per_call():
    verdict = evaluate_novelty(_artifact(), thresholds={"min_added_lenient": 0.9})
    assert _named(verdict, "added_floor").passed is False


def test_the_gate_block_states_that_checks_never_block():
    block = format_novelty_block([evaluate_novelty(_artifact())])
    assert "## Novelty gate" in block
    assert "never block" in block
    assert "added_floor" in block
    assert "- none" in format_novelty_block([])


def test_the_scorecard_block_keeps_cells_and_never_averages_them():
    block = build_novelty_block(
        [_artifact(), _artifact(model="m2", paired={"lenient": {"added": 0.04, "n_paired": 9}})],
        sources=["a.json", "b.json"],
    )
    assert block["status"] == "measured"
    assert block["n_cells"] == 2
    assert [cell["model"] for cell in block["cells"]] == ["m", "m2"]
    assert [cell["source"] for cell in block["cells"]] == ["a.json", "b.json"]
    # No pooled figure anywhere: a mean over (artifact, model) cells would mix two effects.
    assert not any(key.startswith("mean") or key == "added" for key in block)
    assert any("-0.446" in caveat for caveat in block["caveats"])


def test_an_empty_novelty_block_is_not_run_rather_than_zero():
    block = build_novelty_block([])
    assert (block["status"], block["n_cells"], block["cells"]) == ("not_run", 0, [])


def test_cell_warnings_reach_the_subject_warning_list():
    block = build_novelty_block([_artifact(categories={"harmful_conflicting": 3})])
    warnings = novelty_warnings(block)
    assert len(warnings) == 1
    assert "harmful_atoms" in warnings[0]
    assert "skill:skills/accel-tuning" in warnings[0] and "on m" in warnings[0]
    # An info-severity note is not a subject warning.
    assert novelty_warnings(build_novelty_block([_artifact(attribution={})])) == []


def test_an_attached_artifact_must_validate(tmp_path):
    import json

    from dokimi.utils import InputError

    bad = tmp_path / "novelty.json"
    bad.write_text(json.dumps({"schema_version": "novelty.v1"}), encoding="utf-8")
    with pytest.raises(InputError, match=str(bad)):
        load_novelty_artifacts([bad])


def test_an_entry_point_only_run_of_a_bundle_artifact_is_flagged():
    verdict = evaluate_novelty(_artifact(bundle={
        "include_resources": False, "n_files": 1, "entry_point_words": 400,
        "resource_words_measured": 0, "resource_words_on_disk": 9000,
        "outside_share": 0.96, "excluded": [{"path": "references/x.md",
                                             "reason": "resources not requested"}],
    }))
    check = _named(verdict, "artifact_coverage")
    # The value is the share actually not read (9000 of 9400 words), not the reported
    # outside_share, so the two modes are judged on the same quantity.
    assert (check.passed, check.measured, check.value) == (False, True, 0.9574)
    assert "only the entry point was measured" in check.message
    assert "--include-resources" in check.message
    assert verdict.status == "WARN"


def test_a_run_that_threw_away_its_wordings_cannot_pass_quietly():
    verdict = evaluate_novelty(_artifact(probes={
        "n_probes": 60, "malformed_rate": 0.35,
        "by_kind": {"paraphrase": {"generated": 40, "kept": 38, "rejected": 2,
                                   "malformed_rate": 0.05, "reasons": {}},
                    "reversal": {"generated": 40, "kept": 14, "rejected": 26,
                                 "malformed_rate": 0.65, "reasons":
                                 {"polarity_not_flipped": 26}}},
    }))
    check = _named(verdict, "probe_wording_fidelity")
    assert (check.passed, check.value) == (False, 0.65)      # judged on the worst kind
    assert "reversal" in check.message


def test_pointer_spans_are_named_where_dead_tokens_are_reported():
    check = _named(evaluate_novelty(_artifact()), "dead_tokens")
    assert "only name a bundled file" in check.message
    plain = _named(evaluate_novelty(_artifact(bundle={})), "dead_tokens")
    assert "only name a bundled file" not in plain.message


def test_a_bundle_run_that_read_a_fraction_of_the_bundle_does_not_clear_coverage():
    # `performance-patterns` shape: 30k words in 30 files, default 4,000/20,000 budgets.
    verdict = evaluate_novelty(_artifact(bundle={
        "include_resources": True, "n_files": 8, "entry_point_words": 375,
        "resource_words_measured": 6000, "resource_words_on_disk": 30567,
        "outside_share": 0.9879,
        "excluded": [{"path": f"patterns/p{i}.md", "role": "pattern",
                      "reason": "over max_file_words=4000"} for i in range(12)]
        + [{"path": "logo.png", "role": "top", "reason": "not text"}],
    }))
    check = _named(verdict, "artifact_coverage")
    assert check.passed is False
    assert check.value == round((30567 - 6000) / (375 + 30567), 4)
    assert "12 files excluded" in check.message      # the image is not an exclusion
    assert "--max-file-words" in check.message


def test_a_bundle_run_that_read_nearly_all_of_it_clears_coverage():
    verdict = evaluate_novelty(_artifact(bundle={
        "include_resources": True, "n_files": 4, "entry_point_words": 400,
        "resource_words_measured": 880, "resource_words_on_disk": 900,
        "outside_share": 0.69, "excluded": [],
        "pointer_spans": {"n": 2, "tokens": 18},
    }))
    assert _named(verdict, "artifact_coverage").passed is True


def test_an_undecodable_reference_makes_coverage_unmeasurable_not_complete():
    # Its words never enter the on-disk total, so the arithmetic alone would report full
    # coverage of a bundle that was never read.
    verdict = evaluate_novelty(_artifact(bundle={
        "include_resources": True, "n_files": 1, "entry_point_words": 400,
        "resource_words_measured": 0, "resource_words_on_disk": 0, "outside_share": 0.0,
        "unreadable_files": 1,
        "excluded": [{"path": "references/big.md", "role": "reference",
                      "reason": "unreadable"}],
    }))
    check = _named(verdict, "artifact_coverage")
    assert (check.measured, check.value) == (False, None)
    assert "could not be decoded" in check.message


def test_a_partial_bundle_record_is_not_read_as_full_coverage():
    verdict = evaluate_novelty(_artifact(bundle={"outside_share": 0.99}))
    check = _named(verdict, "artifact_coverage")
    assert (check.measured, check.value) == (False, None)


def test_policy_excluded_words_do_not_count_against_coverage():
    verdict = evaluate_novelty(_artifact(bundle={
        "include_resources": True, "n_files": 2, "entry_point_words": 400,
        "resource_words_measured": 100, "resource_words_on_disk": 900,
        "policy_excluded_words": 800, "outside_share": 0.69, "excluded": [],
    }))
    assert _named(verdict, "artifact_coverage").passed is True


def test_the_aggregate_malformed_rate_cannot_be_hidden_by_the_per_kind_rows():
    verdict = evaluate_novelty(_artifact(probes={
        "n_probes": 10, "malformed_rate": 0.9,
        "by_kind": {"paraphrase": {"generated": 10, "rejected": 9}},
    }))
    check = _named(verdict, "probe_wording_fidelity")
    assert (check.passed, check.value) == (False, 0.9)
def test_an_atom_whose_kinds_disagree_is_reported_as_wording_dependent():
    verdict = evaluate_novelty(_artifact(items={"kind_agreement": {
        "full": {"arm": "full", "n_atoms_multi_kind": 40, "n_agree": 24,
                 "agreement": 0.6,
                 "disagreeing": [{"atom_id": "a1",
                                  "by_kind": {"direct": True, "paraphrase": False}}]},
    }}))
    check = _named(verdict, "probe_kind_agreement")
    assert (check.passed, check.value) == (False, 0.6)
    assert "wording-dependent" in check.message


def test_the_kind_agreement_check_takes_the_worse_arm_and_names_it():
    verdict = evaluate_novelty(_artifact(items={"kind_agreement": {
        "no_artifact": {"arm": "no_artifact", "n_atoms_multi_kind": 40, "n_agree": 20,
                        "agreement": 0.5, "disagreeing": []},
        "full": {"arm": "full", "n_atoms_multi_kind": 40, "n_agree": 39,
                 "agreement": 0.975, "disagreeing": []},
    }}))
    check = _named(verdict, "probe_kind_agreement")
    # The docs say the baseline arm is the more interesting read; the gate must agree.
    assert (check.passed, check.value) == (False, 0.5)
    assert "worst arm `no_artifact`" in check.message
    assert "`full` 0.97" in check.message
    assert "wording-matched recall" in check.message


def test_two_files_in_one_arm_are_judged_on_the_worse_one():
    # `novelty items --out` keys rows by source file, so two seeds share an arm.
    verdict = evaluate_novelty(_artifact(items={"kind_agreement": {
        "seed0.json": {"arm": "full", "n_atoms_multi_kind": 10, "n_agree": 2,
                       "agreement": 0.2, "disagreeing": []},
        "seed1.json": {"arm": "full", "n_atoms_multi_kind": 20, "n_agree": 19,
                       "agreement": 0.95, "disagreeing": []},
    }}))
    check = _named(verdict, "probe_kind_agreement")
    assert (check.passed, check.value) == (False, 0.2)


def test_dead_tokens_says_what_extraction_never_read():
    spans = {"frac_carrying_nothing": 0.1, "capped_atom_set": False, "tokens": 100,
             "n_unread": 3, "tokens_unread": 20}
    check = _named(evaluate_novelty(_artifact(spans=spans)), "dead_tokens")
    assert check.measured and check.passed  # 0.80 read: at the floor, so judged
    assert "3 spans (20 of 100 tokens) were never read by extraction" in check.message
    nothing_read = dict(spans, frac_carrying_nothing=0.0, tokens_unread=100)
    check = _named(evaluate_novelty(_artifact(spans=nothing_read)), "dead_tokens")
    assert not check.measured and check.value is None
    # No "outside this figure": with nothing read there is no figure to be outside of.
    assert check.message == ("not checked: extraction read none of the artifact's text "
                             "(3 spans, 100 tokens unread)")


def test_dead_tokens_is_not_judged_on_a_remnant_of_the_text():
    # 49 of 50 spans unread; the one read span carries an atom, so the fraction is 0.0.
    spans = {"frac_carrying_nothing": 0.0, "capped_atom_set": False, "tokens": 100,
             "n_unread": 49, "tokens_unread": 98}
    verdict = evaluate_novelty(_artifact(spans=spans))
    check = _named(verdict, "dead_tokens")
    assert not check.measured and check.passed and check.value == 0.0
    assert "dead_tokens" in verdict.not_measured
    assert check.message == (
        "not checked: extraction read 2% of the artifact's tokens (49 spans, 98 of 100 tokens "
        "unread; floor 80%), so the 0.000 carrying no atom is a figure about the read remnant, "
        "not the artifact"
    )
    just_under = dict(spans, n_unread=3, tokens_unread=21)
    assert not _named(evaluate_novelty(_artifact(spans=just_under)), "dead_tokens").measured
    looser = evaluate_novelty(_artifact(spans=spans), {"min_frac_read": 0.0})
    assert _named(looser, "dead_tokens").measured


@pytest.mark.parametrize("coverage", [
    {"pairs_compared": 50, "pairs_total": 45},
    {"pairs_compared": True, "pairs_total": 45},
    {"pairs_compared": 0, "pairs_total": True},
    {"pairs_compared": -1, "pairs_total": 45},
    {"pairs_compared": 45, "pairs_total": -1},
    {"pairs_compared": "45", "pairs_total": 45},
    {"pairs_compared": 45.0, "pairs_total": 45},
    {"pairs_total": 45},
    "45 of 45",
    None,
])
def test_a_malformed_conflict_coverage_is_not_a_cleared_check(coverage):
    check = _named(evaluate_novelty(_artifact(conflict_coverage=coverage)), "harmful_atoms")
    assert check.measured is False and check.value is None
    assert "conflict_coverage record is malformed" in check.message


def test_a_missing_conflict_coverage_keeps_the_legacy_reading():
    artifact = _artifact()
    artifact.pop("conflict_coverage", None)
    check = _named(evaluate_novelty(artifact), "harmful_atoms")
    assert check.measured is True and check.passed is True
    assert check.message.startswith("0 atoms classified")


def test_partial_coverage_messages_state_the_count_and_skip_the_tail():
    withheld = _named(evaluate_novelty(_artifact(
        conflict_coverage={"pairs_compared": 0, "pairs_total": 3})), "harmful_atoms")
    assert "the conflict check compared 0 of 3 atom pairs" in withheld.message
    assert "batch" not in withheld.message
    assert "Correctness gates before volume" not in withheld.message

    found = _named(evaluate_novelty(_artifact(
        categories={"harmful_conflicting": 1},
        conflict_coverage={"pairs_compared": 20, "pairs_total": 45})), "harmful_atoms")
    assert found.message.startswith(
        "1 atoms classified harmful/conflicting; 20 of 45 atom pairs were compared")
    assert "Correctness gates before volume" in found.message


def test_the_coverage_floor_is_a_threshold():
    artifact = _artifact(conflict_coverage={"pairs_compared": 7139, "pairs_total": 7140})
    strict = _named(evaluate_novelty(artifact), "harmful_atoms")
    assert strict.measured is False
    assert "compared 7139 of 7140 atom pairs (floor 100% of pairs)" in strict.message
    loose = _named(evaluate_novelty(artifact, {"min_conflict_coverage": 0.9}), "harmful_atoms")
    assert loose.measured is True and loose.passed is True
    assert "7139 of 7140 atom pairs were compared" in loose.message


def test_a_capped_atom_set_does_not_clear_harmful_atoms():
    spans = {**_artifact()["spans"], "capped_atom_set": True, "n_atoms_indexed": 30}
    full = {"pairs_compared": 435, "pairs_total": 435}
    capped = _named(evaluate_novelty(_artifact(spans=spans, conflict_coverage=full)),
                    "harmful_atoms")
    assert capped.measured is False and "the atom set was capped (30 indexed)" in capped.message
    # Not a sample, and not always an opening: a cap met on the last span reaches the end.
    assert ("so 0 conflicts among the capped atoms is not a cleared check of the artifact;"
            in capped.message)

    found = _named(evaluate_novelty(_artifact(
        spans=spans, conflict_coverage=full, categories={"harmful_conflicting": 2})),
        "harmful_atoms")
    assert found.measured is True and found.passed is False
    assert "lower bound" in found.message


def test_an_artifact_with_no_check_measured_is_not_a_pass():
    # `{}` carries no stage: every check passed unmeasured and the cell read PASS.
    empty = evaluate_novelty({})
    assert len(empty.not_measured) == len(empty.checks) > 0
    assert empty.status == "NOT MEASURED"
    assert empty.as_dict()["status"] == "NOT MEASURED"
    assert "— NOT MEASURED (10 of 10 not measured)" in format_novelty_block([empty])
    # One measured check is enough for PASS; the rest stay counted as not measured.
    assert evaluate_novelty(_artifact(
        categories={}, shortening=[], attribution={}, controls={}, spans={"n": 60},
        bundle={}, probes={}, items={})).status == "PASS"


@pytest.mark.parametrize("overrides", [
    {"conflict_coverage": {"pairs_compared": 970, "pairs_total": 1770}},
    {"spans": {"capped_atom_set": True, "n_atoms_indexed": 300}},
    {"conflict_coverage": {"pairs_total": 1770}},
], ids=["partial", "capped", "malformed"])
def test_the_reason_a_zero_is_not_cleared_is_the_harmful_atoms_row(overrides):
    artifact = _artifact(**overrides)
    row = next(c for c in evaluate_novelty(artifact).checks if c.name == "harmful_atoms")
    reason = uncleared_conflicts(artifact)
    assert not row.measured and reason and row.message.startswith(f"not measured: {reason}")


@pytest.mark.parametrize("overrides", [
    {},  # every pair compared, nothing capped
    {"categories": {"harmful_conflicting": 2},
     "conflict_coverage": {"pairs_compared": 970, "pairs_total": 1770}},  # found conflicts lead
    {"categories": {}},  # nothing classified: not a 0 to clear
], ids=["cleared", "found", "unclassified"])
def test_there_is_no_uncleared_reason_without_a_zero_left_unchecked(overrides):
    assert uncleared_conflicts(_artifact(**overrides)) is None
