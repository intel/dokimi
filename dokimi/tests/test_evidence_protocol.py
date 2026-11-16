# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""skill_evidence.v1 comparability: the protocol digest and the three conformance fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dokimi.artifacts import load_artifact
from dokimi.subjects.evidence import build_skill_evidence
from dokimi.subjects.evidence_check import (
    build_protocol, compare, comparability, mark_against, protocol_digest, recompute_verdict,
)

CONFORMANCE = Path(__file__).parent / "fixtures" / "evidence_conformance"


def _fixture(name: str) -> dict:
    # Loaded the way a client loads a published file, so each fixture is a valid document too.
    return load_artifact("skill_evidence", CONFORMANCE / name)["results"][0]


# --- the three conformance fixtures -------------------------------------------------------------

def test_fixture_a_verdict_without_its_rule_is_non_comparable():
    result = _fixture("verdict_without_rule.json")

    assert result["verdict"] == "improved"
    assert comparability(result) == ["verdict without verdict_rule"]
    assert recompute_verdict(result) is None


def test_fixture_a_verdict_with_its_rule_recomputes_to_the_same_verdict():
    result = _fixture("verdict_with_rule.json")

    assert comparability(result) == []
    assert recompute_verdict(result) == result["verdict"] == "improved"


def test_fixture_two_judge_snapshots_give_two_digests_and_the_newer_is_not_comparable():
    older, newer = _fixture("judge_snapshot_older.json"), _fixture("judge_snapshot_newer.json")

    assert older["protocol"]["id"] == newer["protocol"]["id"]
    assert older["protocol"]["version"] == newer["protocol"]["version"]
    assert older["protocol"]["digest"] != newer["protocol"]["digest"]
    assert compare(older, older) == []
    assert compare(newer, older) == ["protocol digest differs (judge_model)"]
    [marked] = mark_against([newer], older)
    assert marked["comparable"] is False and marked["non_comparable"]


# --- the digest ---------------------------------------------------------------------------------

def test_the_digest_is_sha256_of_sorted_compact_utf8_json():
    resolved = {"repeats": 3, "judge_model": "j-2026", "judge_sampling": {"temperature": 0.0}}
    expected = hashlib.sha256(
        b'{"judge_model":"j-2026","judge_sampling":{"temperature":0},"repeats":3}').hexdigest()

    assert protocol_digest(resolved) == "sha256:" + expected
    assert build_protocol(resolved)["digest"] == "sha256:" + expected


@pytest.mark.parametrize("alias", ["latest", "claude-sonnet-latest", "gpt:default", "model@stable", "AUTO"])
def test_an_alias_stays_out_of_the_digest_and_is_listed_as_unresolved(alias):
    protocol = build_protocol({"judge_model": alias, "repeats": 3})

    assert protocol["resolved"] == {"repeats": 3}
    assert protocol["unresolved"] == ["judge_model"]
    assert protocol["aliases"] == {"judge_model": alias}
    assert protocol["digest"] == protocol_digest({"repeats": 3})


@pytest.mark.parametrize("snapshot", ["claude-sonnet-5-5", "claude-sonnet-5-5-20260901", "gpt-5.6"])
def test_a_snapshot_id_is_resolved(snapshot):
    assert build_protocol({"judge_model": snapshot})["resolved"] == {"judge_model": snapshot}


def test_a_missing_input_is_unresolved_and_two_unresolved_protocols_are_not_comparable():
    with_rule = _fixture("verdict_with_rule.json")
    vague = {**with_rule, "protocol": build_protocol({**with_rule["protocol"]["resolved"],
                                                       "judge_prompt_sha256": None})}

    assert vague["protocol"]["unresolved"] == ["judge_prompt_sha256"]
    assert compare(vague, vague) == ["protocol inputs not resolved: judge_prompt_sha256"]


def test_a_tampered_resolved_block_is_caught():
    result = _fixture("verdict_with_rule.json")
    result["protocol"]["resolved"]["repeats"] = 1

    assert comparability(result) == ["protocol.digest does not match protocol.resolved"]


# --- the per-result rules -----------------------------------------------------------------------

@pytest.mark.parametrize("drop, reason", [
    ("unit", "verdict without metric_spec.unit"),
    ("higher_is_better", "verdict without metric_spec.higher_is_better"),
])
def test_a_verdict_needs_the_metric_unit_and_direction(drop, reason):
    result = _fixture("verdict_with_rule.json")
    del result["metric_spec"][drop]

    assert comparability(result) == [reason]


def test_a_judged_result_needs_its_judge():
    result = {**_fixture("verdict_with_rule.json"), "judge_model": None}

    assert comparability(result) == ["judged result without judge_model"]


def test_a_verdict_that_contradicts_its_rule_is_non_comparable():
    result = {**_fixture("verdict_with_rule.json"), "p_value": 0.2}

    assert comparability(result) == ["verdict 'improved' does not follow verdict_rule ('inconclusive')"]


def test_lower_is_better_turns_a_negative_delta_into_improved():
    result = _fixture("verdict_with_rule.json")
    result["metric_spec"]["higher_is_better"] = False
    result["delta"] = -6.5

    assert recompute_verdict(result) == "improved"


def test_without_protocols_the_fallback_fields_must_match():
    a = {k: v for k, v in _fixture("verdict_with_rule.json").items() if k != "protocol"}
    b = {**a, "harness": "claude-code"}

    assert compare(a, a) == []
    assert compare(b, a) == ["harness differs"]


# --- what the exporter emits --------------------------------------------------------------------

def test_the_exporter_emits_rule_spec_and_protocol_and_flags_what_it_cannot_compare():
    from test_subjects_evidence import _scorecard

    [skill] = [r for r in build_skill_evidence(_scorecard())["results"] if r["arm"] == "skill:s"]

    assert skill["verdict_rule"]["basis"] == "p_adjusted" and skill["verdict_rule"]["alpha"] == 0.05
    assert skill["metric_spec"]["higher_is_better"] is True and skill["metric_spec"]["unit"]
    assert recompute_verdict(skill) == skill["verdict"]
    assert skill["protocol"]["digest"] == protocol_digest(skill["protocol"]["resolved"])
    # The scorecard records no judge, sampling, judge prompt or repeat count for this cell.
    assert {"judge_model", "judge_prompt_sha256", "judge_sampling", "repeats"} <= set(
        skill["protocol"]["unresolved"])
    assert skill["comparable"] is False
    assert skill["non_comparable"] == comparability(skill) == ["judged result without judge_model"]


def test_a_cell_that_records_its_judge_and_protocol_inputs_is_comparable():
    from test_subjects_evidence import _scorecard

    scorecard = _scorecard()
    scorecard["cells"][0].update({
        "judge_model": "claude-sonnet-5-5-20260901", "judge_provider": "amazon-bedrock",
        "judge_context": "report-snippets", "judge_sampling": {"temperature": 0.0},
        "judge_prompt_sha256": "sha256:" + "d" * 64, "repeats": 3,
    })
    [skill] = [r for r in build_skill_evidence(scorecard)["results"] if r["arm"] == "skill:s"]

    assert skill["comparable"] is True and skill["non_comparable"] == []
    assert skill["protocol"]["unresolved"] == []
    assert skill["protocol"]["resolved"]["judge_model"] == "claude-sonnet-5-5-20260901"
    assert compare(skill, skill) == []


def test_the_rollup_and_scorecard_cells_carry_the_judge(tmp_path: Path):
    from dokimi.eval.matrix_rollup import build_matrix_rollup

    arms = {"baseline_arm": "none", "judge_model": "j", "judge_provider": "jp", "judge_context": "ctx"}
    rollup = build_matrix_rollup(library_name="l", out_dir=tmp_path, compute_plugin_deltas=False,
                                 cell_artifacts=[("c1", tmp_path / "c1.json", arms)])
    cell = rollup["cells"][0]
    assert (cell["judge_model"], cell["judge_provider"], cell["judge_context"]) == ("j", "jp", "ctx")


def test_conformance_fixtures_are_valid_json_files():
    for path in sorted(CONFORMANCE.glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))


# --- self-review round 1 --------------------------------------------------------------------------

@pytest.mark.parametrize("bare", ["opus", "sonnet", "haiku", "gpt-4o", "gpt-4o-mini"])
def test_a_bare_family_name_is_an_alias(bare):
    assert build_protocol({"judge_model": bare})["unresolved"] == ["judge_model"]


def test_an_alias_nested_in_a_value_is_unresolved():
    protocol = build_protocol({"judge_sampling": {"model": "x-latest", "temperature": 0.0}})
    assert protocol["unresolved"] == ["judge_sampling"]


def test_an_integral_float_and_an_int_give_one_digest():
    assert protocol_digest({"t": 0, "r": 3}) == protocol_digest({"t": 0.0, "r": 3.0})


def test_a_p_value_within_rounding_of_alpha_is_not_a_contradiction():
    result = {**_fixture("verdict_with_rule.json"), "p_value": 0.05}
    result["verdict_rule"] = {**result["verdict_rule"], "p_decimals": 4}

    assert recompute_verdict(result) is None
    assert comparability(result) == ["p_value is within its rounding of alpha: the verdict cannot be recomputed"]


def test_the_exporter_keeps_the_judge_temperature_and_prompt_hash_the_run_recorded(tmp_path: Path):
    from test_subjects_evidence import _scorecard
    from dokimi.eval.matrix_rollup import build_matrix_rollup

    arms = {"baseline_arm": "none", "judge_temperature": 0.0, "judge_prompt_sha256": "e" * 64}
    rollup = build_matrix_rollup(library_name="l", out_dir=tmp_path, compute_plugin_deltas=False,
                                 cell_artifacts=[("c1", tmp_path / "c1.json", arms)])
    assert rollup["cells"][0]["judge_temperature"] == 0.0
    assert rollup["cells"][0]["judge_prompt_sha256"] == "e" * 64

    scorecard = _scorecard()
    scorecard["cells"][0].update({"judge_temperature": 0.0, "judge_prompt_sha256": "e" * 64})
    [skill] = [r for r in build_skill_evidence(scorecard)["results"] if r["arm"] == "skill:s"]
    assert skill["protocol"]["resolved"]["judge_sampling"] == {"temperature": 0}
    assert skill["protocol"]["resolved"]["judge_prompt_sha256"] == "e" * 64
    assert skill["verdict_rule"]["p_decimals"] == 4
