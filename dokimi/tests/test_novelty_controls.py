# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Placebo gating, the noise floor, atom-clustered CIs and context framing."""

import json

import pytest

from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.controls import clustered_ci
from dokimi.novelty.measure import ProbeRun, paired_outcomes, run_measurement
from dokimi.novelty.probes import Probe
from dokimi.utils import InputError

RIGHT, WRONG = "correct_complete", "wrong_other"


def _run(atom_id: str, arm: str, verdict: str, question: str = "q") -> ProbeRun:
    return ProbeRun(atom_id=atom_id, probe_kind="direct", arm=arm, question=question,
                    response="r", verdict=verdict)


def test_added_over_placebo_drops_gains_the_placebo_also_gets():
    runs = [
        _run("a", "no_artifact", WRONG), _run("a", "full", RIGHT), _run("a", "placebo", WRONG),
        _run("b", "no_artifact", WRONG), _run("b", "full", RIGHT), _run("b", "placebo", RIGHT),
    ]
    paired = paired_outcomes(runs)
    assert paired["added"] == 1.0
    assert paired["added_over_placebo"]["count"] == 1
    assert paired["added_over_placebo"]["rate"] == 0.5
    assert paired["added_over_placebo"]["n_paired"] == 2


def test_without_control_arms_the_output_has_no_control_keys():
    paired = paired_outcomes([_run("a", "no_artifact", WRONG), _run("a", "full", RIGHT)])
    assert "added_over_placebo" not in paired
    assert "noise_floor" not in paired
    assert paired["ci_cluster"] == "atom"


def test_noise_floor_nets_added_and_flags_degraded_inside_it():
    # With no artifact, a0-a1 flip wrong->right and a9 right->wrong between the two draws.
    base = [WRONG] * 8 + [RIGHT, RIGHT]
    repeat = [RIGHT, RIGHT] + [WRONG] * 6 + [RIGHT, WRONG]
    full = [RIGHT] * 5 + [WRONG] * 3 + [RIGHT, WRONG]
    runs = [_run(f"a{i}", arm, verdicts[i])
            for arm, verdicts in (("no_artifact", base), ("no_artifact_repeat", repeat),
                                  ("full", full))
            for i in range(10)]
    paired = paired_outcomes(runs)
    floor = paired["noise_floor"]
    assert (floor["n_paired"], floor["added"], floor["degraded"]) == (10, 0.2, 0.1)
    assert floor["draws"] == 1 and floor["ci95_added"] is not None
    assert paired["added"] == 0.5
    assert paired["added_net_of_noise"] == 0.3
    assert paired["degraded"] == 0.1
    assert paired["degraded_within_noise"] is True


def test_clustered_ci_is_wider_than_treating_paraphrases_as_independent():
    # Each atom's three paraphrases succeed or fail together.
    per_atom = {f"a{i}": [1.0] * 3 if i % 2 else [0.0] * 3 for i in range(10)}
    flat = {f"p{i}": [v] for i, v in enumerate(x for vs in per_atom.values() for x in vs)}
    lo, hi = clustered_ci(per_atom)
    flat_lo, flat_hi = clustered_ci(flat)
    assert hi - lo > flat_hi - flat_lo
    assert clustered_ci({"a": [1.0], "b": [0.0]}) is None


def _clients(seen):
    def answer(prompt, system):
        seen.append(prompt)
        return "x\nconfidence: 0.5", None
    judge = LLMClient(model="b", transport=lambda p, s: (json.dumps({"verdict": RIGHT}), None))
    return LLMClient(model="a", transport=answer), judge


def _one_probe():
    atom = Atom(statement="s", question="Q?", answer="yes", span_ids=("s0001",))
    return [atom], [Probe(atom_id=atom.id, kind="direct", question="Q?", expected="yes")]


def test_neutral_framing_says_the_text_may_be_irrelevant():
    seen: list[str] = []
    answerer, judge = _clients(seen)
    atoms, probes = _one_probe()
    run_measurement(atoms, probes, "DOC", answerer=answerer, judge=judge,
                    concurrency=1, framing="neutral")
    with_context = [p for p in seen if "DOC" in p]
    assert with_context and with_context[0].startswith("Below is a document. It may or may not")
    with pytest.raises(InputError, match="framing"):
        run_measurement(atoms, probes, "DOC", answerer=answerer, judge=judge, framing="odd")


def test_repeat_arm_asks_the_baseline_again_as_a_new_draw(monkeypatch):
    draws: list[tuple[str, int]] = []

    def transport(self, prompt, system, *, accept=None, draw=0):
        draws.append((prompt, draw))
        return "x\nconfidence: 0.5", None

    monkeypatch.setattr(LLMClient, "_default_transport", transport)
    judge = LLMClient(model="b", transport=lambda p, s: (json.dumps({"verdict": RIGHT}), None))
    atoms, probes = _one_probe()
    result = run_measurement(atoms, probes, "DOC", answerer=LLMClient(model="a"), judge=judge,
                             concurrency=1, arms=("no_artifact", "full", "no_artifact_repeat"))
    assert sorted(d for p, d in draws if "DOC" not in p) == [0, 1]
    assert result.paired("lenient")["noise_floor"]["n_paired"] == 1
