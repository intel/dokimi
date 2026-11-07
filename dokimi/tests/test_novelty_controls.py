# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Placebo gating, the noise floor, atom-clustered CIs and context framing."""

import importlib
import json

import pytest

from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.controls import clustered_ci
from dokimi.novelty.measure import ERRORED, ProbeRun, paired_outcomes, run_measurement
from dokimi.novelty.probes import Probe
from dokimi.novelty.report import build_probe_record
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


def test_derived_controls_read_only_the_probes_all_three_arms_answered():
    # c has no full answer, so the floor (taken over a, b, c) and the net rate must not
    # be compared over different probes: over the common a, b the net is 0.5 - 0.5.
    runs = [
        _run("a", "no_artifact", WRONG), _run("a", "full", RIGHT), _run("a", "no_artifact_repeat", WRONG),
        _run("b", "no_artifact", WRONG), _run("b", "full", WRONG), _run("b", "no_artifact_repeat", RIGHT),
        _run("c", "no_artifact", WRONG), _run("c", "full", ERRORED), _run("c", "no_artifact_repeat", RIGHT),
    ]
    paired = paired_outcomes(runs)
    assert paired["noise_floor"]["added"] == pytest.approx(2 / 3, abs=1e-4)
    assert paired["added"] == 0.5
    assert paired["added_net_of_noise"] == 0.0
    assert paired["degraded_within_noise"] is True


def test_derived_controls_are_omitted_when_no_probe_is_in_all_three_arms():
    runs = [
        _run("a", "no_artifact", WRONG), _run("a", "full", RIGHT), _run("a", "no_artifact_repeat", ERRORED),
        _run("b", "no_artifact", WRONG), _run("b", "full", ERRORED), _run("b", "no_artifact_repeat", RIGHT),
    ]
    paired = paired_outcomes(runs)
    assert "noise_floor" in paired
    assert "added_net_of_noise" not in paired
    assert "degraded_within_noise" not in paired


def test_placebo_with_no_overlap_reports_no_rate_rather_than_zero():
    runs = [
        _run("a", "no_artifact", WRONG), _run("a", "full", RIGHT),
        _run("b", "no_artifact", WRONG), _run("b", "placebo", RIGHT),
    ]
    gated = paired_outcomes(runs)["added_over_placebo"]
    assert (gated["n_paired"], gated["count"], gated["rate"], gated["ci95"]) == (0, 0, None, None)


def test_an_errored_placebo_or_repeat_answer_is_not_a_wrong_answer():
    runs = [
        _run("a", "no_artifact", WRONG), _run("a", "full", RIGHT), _run("a", "placebo", ERRORED),
        _run("b", "no_artifact", WRONG), _run("b", "full", RIGHT), _run("b", "placebo", WRONG),
        _run("a", "no_artifact_repeat", ERRORED), _run("b", "no_artifact_repeat", RIGHT),
    ]
    paired = paired_outcomes(runs)
    assert (paired["added_over_placebo"]["n_paired"], paired["added_over_placebo"]["count"]) == (1, 1)
    assert (paired["noise_floor"]["n_paired"], paired["noise_floor"]["added"]) == (1, 1.0)


def test_degraded_strictly_inside_the_floor_is_within_noise_and_beyond_it_is_not():
    # The floor's degraded rate is 3 of 4 (right then wrong without the artifact): 0.75.
    base = [RIGHT] * 4
    repeat = [RIGHT, WRONG, WRONG, WRONG]
    inside = [RIGHT, WRONG, RIGHT, RIGHT]  # degraded 0.25, strictly inside the floor
    beyond = [WRONG] * 4                   # degraded 1.0, beyond it
    for full, expected in ((inside, True), (beyond, False)):
        runs = [_run(f"a{i}", arm, verdicts[i]) for arm, verdicts in
                (("no_artifact", base), ("no_artifact_repeat", repeat), ("full", full))
                for i in range(4)]
        assert paired_outcomes(runs)["degraded_within_noise"] is expected


def test_clustered_ci_has_a_minimum_of_five_atoms_and_a_pinned_interval():
    five = {"a": [1.0], "b": [0.0], "c": [1.0], "d": [1.0], "e": [0.0]}
    assert clustered_ci({k: five[k] for k in "abcd"}) is None
    assert clustered_ci(five) == [0.2, 1.0]


class _Stub:
    """Empty stand-ins for the measure result and the probe set that the record reads."""

    runs: list = []

    def paired(self, scoring):
        return {}

    def arm_means(self, scoring):
        return {}

    def abstentions(self):
        return {}

    def reversal_resisted(self):
        return None

    def confidence_correlation(self):
        return None

    def as_dict(self):
        return {}


def test_probe_record_names_the_framing_it_was_measured_with():
    record = build_probe_record(
        artifact_ref="skill:x", model="m", judge_model="j", probed_bundle=None,
        atoms_bundle=None, probe_set=_Stub(), measure_result=_Stub(), framing="neutral",
    )
    assert record["context_framing"] == "neutral"
    default = build_probe_record(
        artifact_ref="skill:x", model="m", judge_model="j", probed_bundle=None,
        atoms_bundle=None, probe_set=_Stub(), measure_result=_Stub(),
    )
    assert default["context_framing"] == "reference"


def test_a_scorecard_cell_keeps_the_marker_of_its_interval():
    from dokimi.subjects.novelty_track import novelty_cell

    runs = [_run(f"a{i}", arm, verdict) for i in range(8) for arm, verdict in
            (("no_artifact", WRONG), ("full", RIGHT if i < 5 else WRONG))]
    lenient = paired_outcomes(runs)
    cell = novelty_cell({"paired": {"lenient": lenient}})["paired"]["lenient"]
    assert cell["ci_cluster"] == "atom" and cell["ci95_added"] == lenient["ci95_added"]
    older = {k: v for k, v in lenient.items() if k != "ci_cluster"}
    assert novelty_cell({"paired": {"lenient": older}})["paired"]["lenient"]["ci_cluster"] is None


def _arms_of(verdicts_by_arm):
    return [_run(f"a{i}", arm, v) for arm, vs in verdicts_by_arm.items() for i, v in enumerate(vs)]


def test_a_repeat_arm_the_baseline_never_answered_reports_no_floor():
    runs = _arms_of({"no_artifact": [ERRORED], "full": [RIGHT], "no_artifact_repeat": [RIGHT]})
    assert "noise_floor" not in paired_outcomes(runs)


def test_the_floor_interval_counts_wrong_then_right_flips():
    W, R = WRONG, RIGHT
    runs = _arms_of({"no_artifact": [W, W, W, R, W, W, W, R],
                     "no_artifact_repeat": [R, R, R, W, W, W, W, R], "full": [R] * 8})
    floor = paired_outcomes(runs)["noise_floor"]
    assert (floor["added"], floor["degraded"], floor["ci95_added"]) == (0.375, 0.125, [0.125, 0.75])


def test_added_over_placebo_carries_the_atom_clustered_interval():
    runs = _arms_of({"no_artifact": [WRONG] * 8, "placebo": [WRONG] * 8,
                     "full": [RIGHT, RIGHT] + [WRONG] * 6})
    gated = paired_outcomes(runs)["added_over_placebo"]
    assert (gated["rate"], gated["ci95"]) == (0.25, [0.0, 0.625])


def _skill_with_atoms(tmp_path, monkeypatch, seen):
    from dokimi.cli import build_parser
    from dokimi.commands import novelty as novelty_cmd

    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\n"
                     "Do not enable the legacy driver.\n", encoding="utf-8")
    rows = [{"statement": "The legacy driver must not be enabled.", "kind": "contract",
             "question": "Should the legacy driver be enabled?", "answer": "No.",
             "tags": [], "span_ids": ["s0002"]}]

    def transport(prompt, system=None):
        seen.append(prompt)
        if "Extract the checkable knowledge" in prompt:
            return json.dumps(rows), None
        return json.dumps({"verdict": RIGHT}), None

    real = novelty_cmd._client

    def client(model, provider, role, args):
        built = real(model, provider, role, args)
        built.transport = transport
        return built

    monkeypatch.setattr(novelty_cmd, "_client", client)
    atoms = tmp_path / "atoms.json"
    args = build_parser().parse_args(["skill", "novelty", "atoms", "--artifact", f"skill:{skill}",
                                      "--out", str(atoms)])
    args.func(args)
    return build_parser(), f"skill:{skill}", str(atoms)


def test_novelty_probe_measures_and_records_the_framing_its_flag_names(tmp_path, monkeypatch):
    seen: list[str] = []
    parser, artifact, atoms = _skill_with_atoms(tmp_path, monkeypatch, seen)
    out = tmp_path / "p.json"
    args = parser.parse_args(["skill", "novelty", "probe", "--artifact", artifact, "--atoms", atoms,
                              "--model", "m-a", "--judge-model", "m-b", "--no-paraphrase",
                              "--no-reversal", "--context-framing", "neutral", "--out", str(out)])
    args.func(args)
    assert any(p.startswith("Below is a document. It may or may not") for p in seen)
    assert not any(p.startswith("Reference material:") for p in seen)
    assert json.loads(out.read_text())["context_framing"] == "neutral"


def test_novelty_analyze_hands_its_framing_flag_to_the_run(tmp_path, monkeypatch):
    from dokimi.cli import build_parser

    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n# H\n\nText.\n", encoding="utf-8")
    seen = []

    def stop(text, config, **kwargs):
        seen.append(config.context_framing)
        raise ValueError("stop")

    # The package re-exports analyze(), which hides the module of the same name.
    monkeypatch.setattr(importlib.import_module("dokimi.novelty.analyze"), "analyze", stop)
    args = build_parser().parse_args(["skill", "novelty", "analyze", "--artifact", f"skill:{skill}",
                                      "--context-framing", "neutral"])
    with pytest.raises(SystemExit, match="stop"):
        args.func(args)
    assert seen == ["neutral"]
