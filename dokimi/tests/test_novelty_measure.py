# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The paired 2×2, the controls, and the guards around them — offline, LLM stubbed."""

from __future__ import annotations

import json

import pytest

from dokimi.metrics.usage import UsageRecord
from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient, roll_up_cost
from dokimi.novelty.measure import (
    BASELINE_ARM,
    ProbeRun,
    SelfJudgeError,
    atom_outcomes,
    outcome,
    outcomes_by_atom_kind,
    paired_outcomes,
    run_measurement,
    shuffle_text,
)
from dokimi.novelty.probes import Probe, build_probes


def _atom(statement: str, answer: str = "yes") -> Atom:
    return Atom(
        statement=statement, question=f"Q: {statement}", answer=answer, span_ids=("s0001",)
    )


def _run(
    atom_id: str, arm: str, verdict: str, kind: str = "direct", question: str = "q"
) -> ProbeRun:
    return ProbeRun(
        atom_id=atom_id, probe_kind=kind, arm=arm, question=question,
        response="r", verdict=verdict,
    )


def test_outcome_names_the_four_cells():
    assert outcome(False, True) == "added"
    assert outcome(True, True) == "redundant"
    assert outcome(True, False) == "degraded"
    assert outcome(False, False) == "still_missing"


def test_strict_and_lenient_disagree_on_a_missing_qualifier():
    runs = [
        _run("a1", BASELINE_ARM, "wrong_other"),
        _run("a1", "full", "correct_missing_qualifier"),
    ]
    assert paired_outcomes(runs, scoring="lenient")["added"] == 1
    assert paired_outcomes(runs, scoring="strict")["added"] == 0
    assert paired_outcomes(runs, scoring="strict")["still_missing"] == 1


def test_unpaired_runs_are_dropped_not_counted_as_levels():
    runs = [_run("a1", "full", "correct_complete")]  # no baseline for this probe
    paired = paired_outcomes(runs)
    assert paired["n_paired"] == 0
    assert paired["added"] == 0


def test_reversal_probes_stay_out_of_the_paired_2x2():
    runs = [
        _run("a1", BASELINE_ARM, "wrong_other", kind="reversal"),
        _run("a1", "full", "wrong_reversed", kind="reversal"),
    ]
    assert paired_outcomes(runs)["n_paired"] == 0


def test_atom_outcomes_reports_the_worst_probe_not_the_average():
    runs = [
        _run("a1", BASELINE_ARM, "wrong_other", question="q1"),
        _run("a1", "full", "correct_complete", question="q1"),      # added
        _run("a1", BASELINE_ARM, "correct_complete", question="q2"),
        _run("a1", "full", "wrong_other", question="q2"),           # degraded
    ]
    assert atom_outcomes(runs)["a1"] == "degraded"


def test_knowledge_kind_slices_split_the_2x2_and_never_sum_to_the_whole():
    local = Atom(statement="flag is -x", question="q", answer="a", kind="contract",
                 tags=("local", "version_bound"), span_ids=("s0001",))
    public = Atom(statement="a cache miss stalls", question="q", answer="a",
                  kind="proposition", tags=("public",), span_ids=("s0001",))
    runs = []
    # the local contract is learned from the artifact; the public concept was known
    for i in range(6):
        runs += [_run(local.id, BASELINE_ARM, "wrong_other", question=f"l{i}"),
                 _run(local.id, "full", "correct_complete", question=f"l{i}")]
    for i in range(6):
        runs += [_run(public.id, BASELINE_ARM, "correct_complete", question=f"p{i}"),
                 _run(public.id, "full", "correct_complete", question=f"p{i}")]

    out = outcomes_by_atom_kind(runs, [local, public], scoring="strict")
    assert out["by_kind"]["contract"] == {
        "n_paired": 6, "added": 1.0, "redundant": 0.0, "degraded": 0.0,
        "still_missing": 0.0, "counts": {"added": 6, "redundant": 0, "degraded": 0,
                                         "still_missing": 0}, "k0": 0.0,
    }
    assert out["by_kind"]["proposition"]["k0"] == 1.0
    assert out["by_kind"]["proposition"]["added"] == 0.0
    # one atom carries two tags, so the tag slices overlap and over-count the probes
    assert out["by_tag"]["local"]["n_paired"] == out["by_tag"]["version_bound"]["n_paired"] == 6
    assert sum(v["n_paired"] for v in out["by_tag"].values()) == 18  # vs 12 paired probes


def test_thin_knowledge_slices_report_a_count_and_refuse_to_report_a_rate():
    atom = Atom(statement="x", question="q", answer="a", kind="motif",
                tags=("procedural",), span_ids=("s0001",))
    runs = [_run(atom.id, BASELINE_ARM, "wrong_other", question="q1"),
            _run(atom.id, "full", "correct_complete", question="q1")]
    entry = outcomes_by_atom_kind(runs, [atom])["by_kind"]["motif"]
    assert entry == {"n_paired": 1, "underpowered": True}


def test_knowledge_slices_ignore_reversals_and_atoms_the_extractor_did_not_supply():
    atom = Atom(statement="x", question="q", answer="a", span_ids=("s0001",))
    runs = [_run(atom.id, BASELINE_ARM, "wrong_other", question="q1"),
            _run(atom.id, "full", "correct_complete", question="q1"),
            _run(atom.id, BASELINE_ARM, "wrong_reversed", kind="reversal", question="r1"),
            _run(atom.id, "full", "wrong_reversed", kind="reversal", question="r1"),
            _run("ghost", BASELINE_ARM, "wrong_other", question="g1"),
            _run("ghost", "full", "correct_complete", question="g1")]
    out = outcomes_by_atom_kind(runs, [atom], min_probes=1)
    assert out["by_kind"]["proposition"]["n_paired"] == 1
    assert list(out["by_kind"]) == ["proposition"]


def test_shuffle_is_seeded_and_preserves_content():
    text = "One. Two. Three. Four. Five. Six. Seven. Eight."
    first = shuffle_text(text, seed=7)
    assert first == shuffle_text(text, seed=7)
    assert first != shuffle_text(text, seed=8)
    assert sorted(first.split()) == sorted(text.split())


def test_self_judging_is_refused_by_default():
    same = LLMClient(model="m", transport=lambda p, s: ("x", None))
    with pytest.raises(SelfJudgeError):
        run_measurement([], [], "text", answerer=same, judge=same)


def test_placebo_arm_requires_placebo_text():
    answerer = LLMClient(model="a", transport=lambda p, s: ("x", None))
    judge = LLMClient(model="b", transport=lambda p, s: ("x", None))
    with pytest.raises(ValueError, match="placebo"):
        run_measurement(
            [], [], "text", answerer=answerer, judge=judge,
            arms=(BASELINE_ARM, "full", "placebo"),
        )


def test_baseline_arm_sees_the_question_only():
    """The isolation constraint: no artifact text may reach the baseline prompt."""
    seen: list[str] = []

    def answer(prompt, system):
        seen.append(prompt)
        return "some answer\nconfidence: 0.4", None

    answerer = LLMClient(model="a", transport=answer)
    judge = LLMClient(
        model="b", transport=lambda p, s: (json.dumps({"verdict": "correct_complete"}), None)
    )
    atom = _atom("Flag --fast disables the cache")
    probes = [Probe(atom_id=atom.id, kind="direct", question=atom.question, expected=atom.answer)]
    secret = "SENTINEL-ARTIFACT-TEXT"
    result = run_measurement(
        [atom], probes, secret, answerer=answerer, judge=judge, concurrency=1
    )
    baseline_prompts = [p for p in seen if secret not in p]
    assert baseline_prompts == [f"Question: {atom.question}"]
    assert any(secret in p for p in seen)
    assert result.paired("lenient")["redundant"] == 1
    assert result.paired("lenient")["k0"] == 1.0


def test_confidence_is_recorded_but_never_scored():
    answerer = LLMClient(model="a", transport=lambda p, s: ("nope\nconfidence: 0.9", None))
    judge = LLMClient(
        model="b", transport=lambda p, s: (json.dumps({"verdict": "wrong_other"}), None)
    )
    atom = _atom("x")
    probes = [Probe(atom_id=atom.id, kind="direct", question=atom.question, expected="y")]
    result = run_measurement([atom], probes, "t", answerer=answerer, judge=judge, concurrency=1)
    assert all(r.confidence == 0.9 for r in result.runs)
    assert result.paired("lenient")["still_missing"] == 1  # high confidence, still wrong


def test_probe_builder_falls_back_to_direct_questions_without_a_client():
    atoms = [_atom("a"), _atom("b")]
    probes = build_probes(atoms, client=None)
    assert {p.kind for p in probes} == {"direct"}
    assert len(probes) == 2


def test_cost_rolls_up_per_role_and_stays_none_when_unpriced():
    priced = LLMClient(
        model="a", role="answerer",
        transport=lambda p, s: ("x", UsageRecord(prompt_tokens=10, completion_tokens=2,
                                                 cost_usd=0.5)),
    )
    unpriced = LLMClient(
        model="b", role="judge",
        transport=lambda p, s: ("x", UsageRecord(prompt_tokens=3, completion_tokens=1)),
    )
    priced.text("p")
    unpriced.text("p")
    total = roll_up_cost([priced, unpriced])
    assert total["input_tokens"] == 13
    assert total["llm_calls"] == 2
    assert total["usd"] is None  # one unpriced call must not be read as free
    by_role = {row["role"]: row for row in total["by_role"]}
    assert by_role["answerer"]["usd"] == 0.5
    assert by_role["judge"]["usd"] is None


def test_cost_rolls_up_cache_tokens_and_replays_across_every_role():
    def client(role, records):
        rows = iter(records)
        return LLMClient(model=role, role=role, transport=lambda p, s: ("x", next(rows)))

    extractor = client("extractor", [
        UsageRecord(prompt_tokens=10, completion_tokens=3, cache_read_tokens=4,
                    cache_write_tokens=1, cost_usd=0.1),
        UsageRecord(prompt_tokens=10, completion_tokens=50, cost_usd=0.1, replayed=True),
    ])
    judge = client("judge", [
        UsageRecord(prompt_tokens=20, completion_tokens=4, cache_read_tokens=8,
                    cache_write_tokens=4, cost_usd=0.2),
        UsageRecord(prompt_tokens=20, cost_usd=0.2, replayed=True),
    ])
    for c in (extractor, judge):
        c.text("p")
        c.text("p")
    total = roll_up_cost([extractor, judge])
    # analyze rolls up several roles: a total taken from one of them would pass a
    # single-client test, so each new field is summed over two.
    assert total["llm_calls"] == 4
    assert total["output_tokens"] == 7   # live rows of both roles; replayed 50 left out
    assert total["cache_read_tokens"] == 12
    assert total["cache_write_tokens"] == 5
    assert total["replayed_calls"] == 2


def test_a_failed_call_is_a_dropped_pair_not_a_graded_answer():
    def answer(prompt, system):
        if prompt.startswith("Reference material"):
            raise RuntimeError("context length exceeded")
        return "yes\nconfidence: 0.9", None

    answerer = LLMClient(model="a", transport=answer)
    def grade(prompt, system):
        verdict = "correct_complete" if "RESPONSE: yes" in prompt else "wrong_other"
        return json.dumps({"verdict": verdict}), None

    judge = LLMClient(model="b", transport=grade)
    atom = _atom("x")
    probes = [Probe(atom_id=atom.id, kind="direct", question=atom.question, expected="yes"),
              Probe(atom_id=atom.id, kind="reversal", question="not x?", expected="x",
                    expect_refusal=True)]
    result = run_measurement([atom], probes, "t", answerer=answerer, judge=judge, concurrency=1)
    paired = result.paired("lenient")
    assert (paired["n_paired"], paired["n_unpaired_dropped"], paired["degraded"]) == (0, 1, 0.0)
    assert result.reversal_resisted() is None

    broken_judge = LLMClient(model="b", transport=lambda p, s: (_ for _ in ()).throw(OSError()))
    regraded = run_measurement([atom], probes[:1], "t", answerer=LLMClient(
        model="a", transport=lambda p, s: ("yes", None)), judge=broken_judge, concurrency=1)
    assert regraded.paired("lenient")["n_unpaired_dropped"] == 1


def test_the_headline_pair_is_the_full_arm_whatever_the_arms_order():
    from dokimi.novelty.measure import parse_arms

    answerer = LLMClient(model="a", transport=lambda p, s: ("x", None))
    judge = LLMClient(model="b", transport=lambda p, s: ('{"verdict": "wrong_other"}', None))
    result = run_measurement([], [], "t", answerer=answerer, judge=judge,
                             arms=(BASELINE_ARM, "placebo", "full"), placebo_text="p")
    assert result.with_arm == "full"
    with pytest.raises(ValueError, match="full"):
        run_measurement([], [], "t", answerer=answerer, judge=judge, arms=(BASELINE_ARM,))
    with pytest.raises(ValueError, match="full"):
        parse_arms("no_artifact,placebo")
